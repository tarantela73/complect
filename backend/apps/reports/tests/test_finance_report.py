"""Tests for finance report query and Excel export endpoint (U4)."""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from apps.common.tenancy.context import set_tenant_context
from apps.common.tenancy.context_core import RequestContext, set_context
from apps.contracts.models.contract import Contract, ResourceSupplyType
from apps.finance.models.transaction import Transaction, TransactionStatus, TransactionType
from apps.reports.queries.finance_report_query import get_finance_report_rows


def _ctx(tenant_id: uuid.UUID) -> None:
    set_context(RequestContext(
        tenant_id=tenant_id,
        user_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
    ))
    set_tenant_context(tenant_id)


def _make_contract(tenant_id: uuid.UUID) -> Contract:
    c = Contract(
        contract_number=f"C-{uuid.uuid4().hex[:6].upper()}",
        title="Test",
        status="ACTIVE",
        resource_type=ResourceSupplyType.ELECTRICITY,
        total_amount=500_00,
        paid_amount=0,
        tenant_id=tenant_id,
    )
    c.save()
    return c


def _make_txn(
    tenant_id: uuid.UUID,
    contract_id: uuid.UUID,
    status: str = "POSTED",
    txn_type: str = "PAYMENT",
    amount: int = 100_00,
    value_date: date | None = None,
    counterparty_inn: str = "",
    external_ref: str = "",
) -> Transaction:
    t = Transaction(
        contract_id=contract_id,
        transaction_type=txn_type,
        status=status,
        amount=amount,
        value_date=value_date or date(2026, 1, 15),
        counterparty_inn=counterparty_inn,
        external_ref=external_ref,
        tenant_id=tenant_id,
    )
    t.save()
    return t


@pytest.mark.django_db
class TestFinanceReportQuery:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.tenant_id = uuid.uuid4()
        _ctx(self.tenant_id)
        self.contract = _make_contract(self.tenant_id)

    def test_returns_all_tenant_transactions(self):
        _make_txn(self.tenant_id, self.contract.id)
        _make_txn(self.tenant_id, self.contract.id)

        rows = get_finance_report_rows()
        assert len(rows) == 2

    def test_date_from_filter(self):
        _make_txn(self.tenant_id, self.contract.id, value_date=date(2026, 1, 1))
        _make_txn(self.tenant_id, self.contract.id, value_date=date(2026, 3, 1))

        rows = get_finance_report_rows(date_from=date(2026, 2, 1))
        assert len(rows) == 1

    def test_date_to_filter(self):
        _make_txn(self.tenant_id, self.contract.id, value_date=date(2026, 1, 1))
        _make_txn(self.tenant_id, self.contract.id, value_date=date(2026, 3, 1))

        rows = get_finance_report_rows(date_to=date(2026, 2, 1))
        assert len(rows) == 1

    def test_reversed_transactions_included_without_filter(self):
        _make_txn(self.tenant_id, self.contract.id, status="POSTED")
        _make_txn(self.tenant_id, self.contract.id, status="REVERSED")

        rows = get_finance_report_rows()
        assert len(rows) == 2

    def test_status_filter(self):
        _make_txn(self.tenant_id, self.contract.id, status="POSTED")
        _make_txn(self.tenant_id, self.contract.id, status="REVERSED")

        rows = get_finance_report_rows(status="REVERSED")
        assert len(rows) == 1
        assert rows[0]["status"] == "REVERSED"

    def test_contract_join_populates_contract_number(self):
        _make_txn(self.tenant_id, self.contract.id)

        rows = get_finance_report_rows()
        assert rows[0]["contract_number"] == self.contract.contract_number

    def test_tenant_isolation(self):
        other_tenant = uuid.uuid4()
        other_contract = _make_contract(other_tenant)
        _make_txn(self.tenant_id, self.contract.id)
        _make_txn(other_tenant, other_contract.id)

        rows = get_finance_report_rows()
        assert len(rows) == 1

    def test_empty_result(self):
        rows = get_finance_report_rows()
        assert rows == []
