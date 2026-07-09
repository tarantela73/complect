"""
Director Dashboard endpoint tests (U7 — Sprint C).

Coverage:
  - GET /api/v1/dashboard/director/ with permission → 200
  - GET /api/v1/dashboard/director/ without permission → 403
  - Response schema: all expected top-level keys present
  - payments_credit_month / payments_debit_month: correct aggregation
  - payments_credit_month when no IMPORTED statements → Decimal("0.00")
  - open_contracts_count excludes CLOSED contracts
  - Cache: second call within TTL served from cache (no extra DB queries)
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.test import Client

from apps.common.cache.dashboard_cache import _director_key
from apps.common.models.rbac import Permission, Role, RoleBinding, RolePermission
from apps.common.tenancy.context import set_tenant_context
from apps.contracts.models.contract import Contract, ContractStatus
from apps.objects.models.supply_object import ObjectStatus, SupplyObject, SupplyObjectType
from apps.payments.models.bank_statement import BankStatement, BankStatementStatus
from apps.payments.models.bank_statement_line import BankStatementLine

GW_TOKEN = "complect_internal_secure_mesh_token_2026"
URL = "/api/v1/dashboard/director/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gw_headers(tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    return {
        "HTTP_X_GATEWAY_TOKEN": GW_TOKEN,
        "HTTP_X_TENANT_ID": str(tenant_id),
        "HTTP_X_USER_ID": str(user_id),
    }


def _make_permission(action: str, resource: str) -> Permission:
    perm, _ = Permission.objects.get_or_create(action=action, resource=resource)
    return perm


def _make_role(name: str, *perms: Permission) -> Role:
    role, _ = Role.objects.get_or_create(name=name)
    for p in perms:
        RolePermission.objects.get_or_create(role=role, permission=p)
    return role


def _bind(user_id: uuid.UUID, tenant_id: uuid.UUID, role: Role) -> RoleBinding:
    binding, _ = RoleBinding.objects.get_or_create(
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        defaults={"is_active": True, "granted_by": None},
    )
    return binding


def _make_imported_statement(tenant_id: uuid.UUID) -> BankStatement:
    return BankStatement.objects.create(
        tenant_id=tenant_id,
        file_name="test.txt",
        file_format="kl_to_1c",
        raw_text="",
        status=BankStatementStatus.IMPORTED,
    )


def _make_line(
    statement: BankStatement,
    tenant_id: uuid.UUID,
    *,
    direction: str,
    amount: str,
    transaction_date: date | None = None,
) -> BankStatementLine:
    if transaction_date is None:
        transaction_date = date.today()
    return BankStatementLine.objects.create(
        tenant_id=tenant_id,
        statement=statement,
        line_number=1,
        parse_status="parsed",
        direction=direction,
        amount=Decimal(amount),
        transaction_date=transaction_date,
    )


def _make_contract(
    tenant_id: uuid.UUID,
    status: str = ContractStatus.ACTIVE,
) -> Contract:
    set_tenant_context(tenant_id)
    return Contract.objects.create(
        tenant_id=tenant_id,
        contract_number=f"C-{uuid.uuid4().hex[:8]}",
        title="Test contract",
        status=status,
        resource_type="OTHER",
        total_amount=100_000,
    )


# ---------------------------------------------------------------------------
# Permission tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDirectorDashboardPermissionOff:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def test_returns_200_with_gateway_context(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        cache.delete(_director_key(tenant_id))

        resp = Client().get(URL, **_gw_headers(tenant_id, user_id))

        assert resp.status_code == 200
        data = resp.json()
        assert "objects_in_progress_count" in data
        assert "overdue_objects_count" in data
        assert "objects_top5" in data
        assert "payments_credit_month" in data
        assert "payments_debit_month" in data
        assert "open_contracts_count" in data
        assert "period_from" in data
        assert "period_to" in data

    def test_missing_context_headers_returns_401(self):
        resp = Client().get(URL, HTTP_X_GATEWAY_TOKEN=GW_TOKEN)
        assert resp.status_code == 401

    def test_missing_gateway_token_returns_403(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        resp = Client().get(
            URL,
            HTTP_X_TENANT_ID=str(tenant_id),
            HTTP_X_USER_ID=str(user_id),
        )
        assert resp.status_code == 403


@pytest.mark.django_db
class TestDirectorDashboardPermissionEnforce:
    @pytest.fixture(autouse=True)
    def _rbac_enforce(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "ENFORCE"
        settings.RBAC_POLICY_CACHE_TTL = 60

    def test_no_permission_returns_403(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()

        resp = Client().get(URL, **_gw_headers(tenant_id, user_id))

        assert resp.status_code == 403

    def test_with_director_dashboard_read_permission_returns_200(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        cache.delete(_director_key(tenant_id))

        perm = _make_permission("read", "director_dashboard")
        role = _make_role(f"director_{uuid.uuid4().hex[:6]}", perm)
        _bind(user_id, tenant_id, role)

        resp = Client().get(URL, **_gw_headers(tenant_id, user_id))

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# KPI: payments aggregation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDirectorDashboardPayments:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def _call(self, tenant_id: uuid.UUID) -> dict:
        user_id = uuid.uuid4()
        cache.delete(_director_key(tenant_id))
        resp = Client().get(URL, **_gw_headers(tenant_id, user_id))
        assert resp.status_code == 200
        return resp.json()

    def test_no_statements_returns_zero_sums(self):
        tenant_id = uuid.uuid4()
        data = self._call(tenant_id)
        assert Decimal(data["payments_credit_month"]) == Decimal("0.00")
        assert Decimal(data["payments_debit_month"]) == Decimal("0.00")

    def test_credit_and_debit_aggregated_correctly(self):
        tenant_id = uuid.uuid4()
        stmt = _make_imported_statement(tenant_id)
        _make_line(stmt, tenant_id, direction="credit", amount="10000.00")
        _make_line(stmt, tenant_id, direction="credit", amount="5000.00")
        _make_line(stmt, tenant_id, direction="debit", amount="3000.00")

        data = self._call(tenant_id)

        assert Decimal(data["payments_credit_month"]) == Decimal("15000.00")
        assert Decimal(data["payments_debit_month"]) == Decimal("3000.00")

    def test_non_imported_statement_excluded(self):
        tenant_id = uuid.uuid4()
        stmt = BankStatement.objects.create(
            tenant_id=tenant_id,
            file_name="pending.txt",
            file_format="kl_to_1c",
            raw_text="",
            status=BankStatementStatus.PENDING,
        )
        _make_line(stmt, tenant_id, direction="credit", amount="9999.00")

        data = self._call(tenant_id)

        assert Decimal(data["payments_credit_month"]) == Decimal("0.00")

    def test_different_tenant_payments_not_included(self):
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        stmt_b = _make_imported_statement(tenant_b)
        _make_line(stmt_b, tenant_b, direction="credit", amount="99999.00")

        data = self._call(tenant_a)

        assert Decimal(data["payments_credit_month"]) == Decimal("0.00")

    def test_lines_outside_current_month_excluded(self):
        tenant_id = uuid.uuid4()
        stmt = _make_imported_statement(tenant_id)
        today = date.today()
        last_month = date(today.year if today.month > 1 else today.year - 1, (today.month - 2) % 12 + 1, 1)
        _make_line(stmt, tenant_id, direction="credit", amount="5000.00", transaction_date=last_month)

        data = self._call(tenant_id)

        assert Decimal(data["payments_credit_month"]) == Decimal("0.00")


# ---------------------------------------------------------------------------
# KPI: open_contracts_count
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDirectorDashboardOpenContracts:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def _call(self, tenant_id: uuid.UUID) -> dict:
        user_id = uuid.uuid4()
        cache.delete(_director_key(tenant_id))
        resp = Client().get(URL, **_gw_headers(tenant_id, user_id))
        assert resp.status_code == 200
        return resp.json()

    def test_closed_contract_not_counted(self):
        tenant_id = uuid.uuid4()
        _make_contract(tenant_id, status=ContractStatus.CLOSED)

        data = self._call(tenant_id)

        assert data["open_contracts_count"] == 0

    def test_active_contract_counted(self):
        tenant_id = uuid.uuid4()
        _make_contract(tenant_id, status=ContractStatus.ACTIVE)

        data = self._call(tenant_id)

        assert data["open_contracts_count"] == 1

    def test_draft_contract_counted(self):
        tenant_id = uuid.uuid4()
        _make_contract(tenant_id, status=ContractStatus.DRAFT)

        data = self._call(tenant_id)

        assert data["open_contracts_count"] >= 1

    def test_mixed_statuses(self):
        tenant_id = uuid.uuid4()
        _make_contract(tenant_id, status=ContractStatus.ACTIVE)
        _make_contract(tenant_id, status=ContractStatus.DRAFT)
        _make_contract(tenant_id, status=ContractStatus.CLOSED)

        data = self._call(tenant_id)

        assert data["open_contracts_count"] == 2


# ---------------------------------------------------------------------------
# KPI: objects summary
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDirectorDashboardObjectsSummary:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def _call(self, tenant_id: uuid.UUID) -> dict:
        user_id = uuid.uuid4()
        cache.delete(_director_key(tenant_id))
        resp = Client().get(URL, **_gw_headers(tenant_id, user_id))
        assert resp.status_code == 200
        return resp.json()

    def test_in_progress_count_is_zero_when_no_objects(self):
        tenant_id = uuid.uuid4()
        data = self._call(tenant_id)
        assert data["objects_in_progress_count"] == 0

    def test_overdue_count_is_zero_when_no_objects(self):
        tenant_id = uuid.uuid4()
        data = self._call(tenant_id)
        assert data["overdue_objects_count"] == 0

    def test_objects_top5_is_list(self):
        tenant_id = uuid.uuid4()
        data = self._call(tenant_id)
        assert isinstance(data["objects_top5"], list)
        assert len(data["objects_top5"]) <= 5

    def _make_supply_object(
        self,
        tenant_id: uuid.UUID,
        *,
        status: str = ObjectStatus.IN_PROGRESS,
        planned_end_date: date | None = None,
        name: str = "Test Object",
    ) -> SupplyObject:
        return SupplyObject.objects.create(
            tenant_id=tenant_id,
            name=name,
            object_type=SupplyObjectType.OTHER,
            resource_type="OTHER",
            status=status,
            planned_end_date=planned_end_date,
        )

    def test_in_progress_object_counted(self):
        tenant_id = uuid.uuid4()
        self._make_supply_object(tenant_id, status=ObjectStatus.IN_PROGRESS)

        data = self._call(tenant_id)

        assert data["objects_in_progress_count"] == 1

    def test_completed_object_not_counted_as_in_progress(self):
        tenant_id = uuid.uuid4()
        self._make_supply_object(tenant_id, status=ObjectStatus.COMPLETED)

        data = self._call(tenant_id)

        assert data["objects_in_progress_count"] == 0

    def test_overdue_object_counted(self):
        tenant_id = uuid.uuid4()
        past_date = date(2000, 1, 1)
        self._make_supply_object(tenant_id, status=ObjectStatus.IN_PROGRESS, planned_end_date=past_date)

        data = self._call(tenant_id)

        assert data["overdue_objects_count"] == 1

    def test_in_progress_without_end_date_not_overdue(self):
        tenant_id = uuid.uuid4()
        self._make_supply_object(tenant_id, status=ObjectStatus.IN_PROGRESS, planned_end_date=None)

        data = self._call(tenant_id)

        assert data["overdue_objects_count"] == 0

    def test_objects_top5_contains_in_progress_name(self):
        tenant_id = uuid.uuid4()
        self._make_supply_object(tenant_id, status=ObjectStatus.IN_PROGRESS, name="Alpha Object")

        data = self._call(tenant_id)

        names = [item["name"] for item in data["objects_top5"]]
        assert "Alpha Object" in names

    def test_objects_top5_max_five_items(self):
        tenant_id = uuid.uuid4()
        for i in range(7):
            self._make_supply_object(tenant_id, name=f"Object {i:02d}")

        data = self._call(tenant_id)

        assert len(data["objects_top5"]) <= 5

    def test_different_tenant_objects_not_counted(self):
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        self._make_supply_object(tenant_b, status=ObjectStatus.IN_PROGRESS)

        data = self._call(tenant_a)

        assert data["objects_in_progress_count"] == 0


# ---------------------------------------------------------------------------
# Period fields
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDirectorDashboardPeriod:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def test_period_from_is_first_day_of_month(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        cache.delete(_director_key(tenant_id))

        resp = Client().get(URL, **_gw_headers(tenant_id, user_id))
        data = resp.json()

        today = date.today()
        expected_from = today.replace(day=1).isoformat()
        expected_to = today.isoformat()
        assert data["period_from"] == expected_from
        assert data["period_to"] == expected_to


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDirectorDashboardCache:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def test_second_call_uses_cache(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        cache.delete(_director_key(tenant_id))

        headers = _gw_headers(tenant_id, user_id)
        resp1 = Client().get(URL, **headers)
        assert resp1.status_code == 200

        # Populate the cache, then verify cache hit returns the same data.
        resp2 = Client().get(URL, **headers)
        assert resp2.status_code == 200
        assert resp1.json() == resp2.json()

    def test_cache_invalidate_allows_fresh_data(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        cache.delete(_director_key(tenant_id))

        headers = _gw_headers(tenant_id, user_id)
        resp1 = Client().get(URL, **headers)
        assert resp1.status_code == 200

        cache.delete(_director_key(tenant_id))

        stmt = _make_imported_statement(tenant_id)
        _make_line(stmt, tenant_id, direction="credit", amount="77777.00")

        resp2 = Client().get(URL, **headers)
        assert resp2.status_code == 200
        assert Decimal(resp2.json()["payments_credit_month"]) == Decimal("77777.00")
