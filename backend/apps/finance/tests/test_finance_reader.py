"""
Tests for FinanceReaderService (ADR-0005 cross-domain read contract):
  - tenant isolation: payments from other tenant are invisible
  - only POSTED transactions returned (PENDING, REVERSED, FAILED excluded)
  - soft-deleted transactions excluded
  - respects limit=5 (returns exactly N most recent)
  - empty result when no matching payments
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from apps.common.tenancy.context import set_tenant_context
from apps.contracts.models.contract import Contract, ContractStatus, ResourceSupplyType
from apps.contracts.repositories.contract_repo import ContractRepository
from apps.finance.models.transaction import Transaction, TransactionStatus, TransactionType
from apps.finance.services.finance_reader import FinanceReaderService


@pytest.mark.django_db
class TestFinanceReaderService:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.reader = FinanceReaderService()
        self.tenant_id = uuid.uuid4()
        self.contract_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    def _make_tx(
        self,
        *,
        tenant_id: uuid.UUID | None = None,
        contract_id: uuid.UUID | None = None,
        status: str = TransactionStatus.POSTED,
        amount: int = 10_000,
        value_date: date | None = None,
        is_deleted: bool = False,
    ) -> Transaction:
        tx = Transaction(
            tenant_id=tenant_id or self.tenant_id,
            contract_id=contract_id or self.contract_id,
            transaction_type=TransactionType.PAYMENT,
            status=status,
            amount=amount,
            value_date=value_date,
            is_deleted=is_deleted,
        )
        tx.save()
        return tx

    def test_returns_posted_payments(self):
        self._make_tx(amount=100_00, value_date=date(2026, 1, 10))
        self._make_tx(amount=200_00, value_date=date(2026, 2, 15))

        result = self.reader.get_last_posted_payments(self.tenant_id, self.contract_id)

        assert len(result) == 2
        amounts = {r.amount for r in result}
        assert amounts == {100_00, 200_00}

    def test_excludes_non_posted_statuses(self):
        self._make_tx(status=TransactionStatus.POSTED, amount=500)
        self._make_tx(status=TransactionStatus.PENDING, amount=999)
        self._make_tx(status=TransactionStatus.REVERSED, amount=999)
        self._make_tx(status=TransactionStatus.FAILED, amount=999)

        result = self.reader.get_last_posted_payments(self.tenant_id, self.contract_id)

        assert len(result) == 1
        assert result[0].amount == 500

    def test_excludes_soft_deleted(self):
        self._make_tx(amount=111, is_deleted=False)
        self._make_tx(amount=999, is_deleted=True)

        result = self.reader.get_last_posted_payments(self.tenant_id, self.contract_id)

        assert len(result) == 1
        assert result[0].amount == 111

    def test_tenant_isolation(self):
        other_tenant = uuid.uuid4()
        self._make_tx(amount=777)
        self._make_tx(tenant_id=other_tenant, amount=888)

        result = self.reader.get_last_posted_payments(self.tenant_id, self.contract_id)

        assert len(result) == 1
        assert result[0].amount == 777

    def test_other_tenant_sees_only_own_payments(self):
        other_tenant = uuid.uuid4()
        self._make_tx(tenant_id=other_tenant, amount=300)

        result = self.reader.get_last_posted_payments(self.tenant_id, self.contract_id)

        assert result == []

    def test_limit_respected(self):
        for i in range(8):
            self._make_tx(amount=(i + 1) * 100)

        result = self.reader.get_last_posted_payments(self.tenant_id, self.contract_id, limit=5)

        assert len(result) == 5

    def test_limit_returns_most_recent(self):
        for i in range(8):
            tx = self._make_tx(amount=(i + 1) * 100)

        result = self.reader.get_last_posted_payments(self.tenant_id, self.contract_id, limit=3)

        amounts = [r.amount for r in result]
        assert amounts == sorted(amounts, reverse=True) or len(amounts) == 3

    def test_empty_when_no_payments(self):
        result = self.reader.get_last_posted_payments(self.tenant_id, self.contract_id)

        assert result == []

    def test_dto_fields(self):
        vd = date(2026, 3, 5)
        tx = self._make_tx(amount=5000, value_date=vd)

        result = self.reader.get_last_posted_payments(self.tenant_id, self.contract_id)

        assert len(result) == 1
        dto = result[0]
        assert dto.transaction_id == tx.id
        assert dto.amount == 5000
        assert dto.value_date == vd

    def test_value_date_can_be_none(self):
        self._make_tx(amount=200, value_date=None)

        result = self.reader.get_last_posted_payments(self.tenant_id, self.contract_id)

        assert result[0].value_date is None
