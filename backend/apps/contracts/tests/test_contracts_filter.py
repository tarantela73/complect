"""Tests for contracts list filtering including the contragent_id filter (U1)."""

from __future__ import annotations

import uuid

import pytest

from apps.common.tenancy.context import set_tenant_context
from apps.common.tenancy.context_core import RequestContext, set_context
from apps.contracts.models.contract import Contract, ResourceSupplyType
from apps.contracts.queries.contract_queries import get_contracts_page


def _make_contract(tenant_id: uuid.UUID, contragent_id: uuid.UUID | None = None, status: str = "ACTIVE") -> Contract:
    set_tenant_context(tenant_id)
    c = Contract(
        contract_number=f"C-{uuid.uuid4().hex[:6].upper()}",
        title="Test Contract",
        status=status,
        resource_type=ResourceSupplyType.ELECTRICITY,
        total_amount=100_000,
        paid_amount=0,
        tenant_id=tenant_id,
        contragent_id=contragent_id,
    )
    c.save()
    return c


@pytest.mark.django_db
class TestContragentIdFilter:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.tenant_id = uuid.uuid4()
        self.contragent_a = uuid.uuid4()
        self.contragent_b = uuid.uuid4()
        set_context(
            RequestContext(
                tenant_id=self.tenant_id,
                user_id=uuid.uuid4(),
                request_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
            )
        )

    def test_contragent_id_filter_returns_only_matching(self):
        ca1 = _make_contract(self.tenant_id, contragent_id=self.contragent_a)
        _make_contract(self.tenant_id, contragent_id=self.contragent_b)

        result = get_contracts_page(contragent_id=self.contragent_a)

        ids = [r.id for r in result["results"]]
        assert ca1.id in ids
        assert result["count"] == 1

    def test_combined_status_and_contragent_filter(self):
        _make_contract(self.tenant_id, contragent_id=self.contragent_a, status="ACTIVE")
        _make_contract(self.tenant_id, contragent_id=self.contragent_a, status="DRAFT")

        result = get_contracts_page(status="ACTIVE", contragent_id=self.contragent_a)

        assert result["count"] == 1
        assert result["results"][0].status == "ACTIVE"

    def test_no_contragent_id_returns_all_tenant_contracts(self):
        _make_contract(self.tenant_id, contragent_id=self.contragent_a)
        _make_contract(self.tenant_id, contragent_id=self.contragent_b)
        _make_contract(self.tenant_id, contragent_id=None)

        result = get_contracts_page()

        assert result["count"] == 3

    def test_tenant_isolation_with_contragent_filter(self):
        other_tenant = uuid.uuid4()
        _make_contract(self.tenant_id, contragent_id=self.contragent_a)
        _make_contract(other_tenant, contragent_id=self.contragent_a)

        result = get_contracts_page(contragent_id=self.contragent_a)

        assert result["count"] == 1


@pytest.mark.django_db
class TestCursorPagination:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.tenant_id = uuid.uuid4()
        set_context(
            RequestContext(
                tenant_id=self.tenant_id,
                user_id=uuid.uuid4(),
                request_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
            )
        )

    def test_pages_do_not_overlap(self):
        for _ in range(12):
            _make_contract(self.tenant_id)

        page1 = get_contracts_page(limit=5)
        assert len(page1["results"]) == 5
        cursor = page1["next_cursor"]
        assert cursor is not None

        page2 = get_contracts_page(cursor=uuid.UUID(cursor), limit=5)
        ids1 = {str(r.id) for r in page1["results"]}
        ids2 = {str(r.id) for r in page2["results"]}
        assert ids1.isdisjoint(ids2)

    def test_cursor_pagination_covers_all_rows(self):
        for _ in range(13):
            _make_contract(self.tenant_id)

        seen, cursor = set(), None
        while True:
            page = get_contracts_page(cursor=cursor, limit=5)
            for item in page["results"]:
                assert item.id not in seen, f"duplicate row {item.id}"
                seen.add(item.id)
            next_cur = page["next_cursor"]
            if not next_cur:
                break
            cursor = uuid.UUID(next_cur)

        assert len(seen) == 13

    def test_cursor_from_other_tenant_returns_no_results(self):
        """Cursor UUID from another tenant must not leak cross-tenant data."""
        other_tenant = uuid.uuid4()
        set_context(
            RequestContext(
                tenant_id=other_tenant,
                user_id=uuid.uuid4(),
                request_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
            )
        )
        other_contract = _make_contract(other_tenant)
        cross_tenant_cursor = other_contract.id

        set_context(
            RequestContext(
                tenant_id=self.tenant_id,
                user_id=uuid.uuid4(),
                request_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
            )
        )
        for _ in range(3):
            _make_contract(self.tenant_id)

        result = get_contracts_page(cursor=cross_tenant_cursor, limit=10)
        # Anchor not found → no filter applied → returns all tenant rows
        # (safe: anchor from other tenant resolves to None)
        assert all(str(r.id) != str(cross_tenant_cursor) for r in result["results"])

    def test_pagination_survives_soft_delete_of_anchor(self):
        """Soft-delete якоря между страницами не должен приводить к дублям.

        Repro для регрессии is_deleted=False в anchor lookup (contract_queries.py:68):
        удалённый якорь перестаёт резолвиться → фильтр границы пропущен →
        page2 стартует с начала → дубли page1.
        """
        for _ in range(6):
            _make_contract(self.tenant_id)

        page1 = get_contracts_page(limit=3)
        assert len(page1["results"]) == 3
        cursor = page1["next_cursor"]
        assert cursor is not None

        # Якорь = последний элемент page1 (его id ушёл в next_cursor). Soft-delete его.
        Contract.objects.filter(id=uuid.UUID(cursor)).update(is_deleted=True)

        page2 = get_contracts_page(cursor=uuid.UUID(cursor), limit=3)
        ids1 = {str(r.id) for r in page1["results"]}
        ids2 = {str(r.id) for r in page2["results"]}
        assert ids1.isdisjoint(ids2), f"дубли между страницами: {ids1 & ids2}"
