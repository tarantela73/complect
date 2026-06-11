"""Tests for contracts list filtering including the contragent_id filter (U1)."""
from __future__ import annotations

import uuid

import pytest

from apps.common.tenancy.context import set_tenant_context
from apps.common.tenancy.context_core import RequestContext, set_context
from apps.contracts.models.contract import Contract, ContractStatus, ResourceSupplyType
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
        set_context(RequestContext(
            tenant_id=self.tenant_id,
            user_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
        ))

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
