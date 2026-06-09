"""
Tests for MatchingService reconciliation algorithm:
  - Strategy 1: exact doc_number → MATCHED(100) / AMBIGUOUS(50)
  - Strategy 2: amount + date window → MATCHED(85) / AMBIGUOUS(50)
  - Strategy 3: amount + INN → PARTIAL(60) / AMBIGUOUS(40)
  - No signal → UNMATCHED(0)
  - Precedence: strategy 1 wins; falls through only when no results
  - Edge cases: None amount, missing fields, bulk_update counters
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from apps.common.tenancy.context import set_tenant_context
from apps.common.contracts.finance_contracts import TxCandidateDTO
from apps.payments.models.bank_statement import BankStatement, BankStatementStatus
from apps.payments.models.bank_statement_line import BankStatementLine, MatchStatus
from apps.payments.services.matching_service import MatchingService


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tenant() -> uuid.UUID:
    tid = uuid.uuid4()
    set_tenant_context(tid)
    return tid


def _make_statement(tenant_id: uuid.UUID) -> BankStatement:
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
    amount: str | None = "50000.00",
    doc_number: str = "PAY-001",
    counterparty_inn: str = "7700000001",
    transaction_date: date | None = date(2025, 1, 15),
) -> BankStatementLine:
    return BankStatementLine.objects.create(
        tenant_id=tenant_id,
        statement=statement,
        line_number=1,
        parse_status="parsed",
        amount=Decimal(amount) if amount is not None else None,
        doc_number=doc_number,
        counterparty_inn=counterparty_inn,
        transaction_date=transaction_date,
    )


def _dto(amount_kopecks: int = 5_000_000) -> TxCandidateDTO:
    return TxCandidateDTO(
        transaction_id=uuid.uuid4(),
        contract_id=uuid.uuid4(),
        amount_kopecks=amount_kopecks,
        external_ref="PAY-001",
        counterparty_inn="7700000001",
        value_date=date(2025, 1, 15),
    )


def _finder(
    *,
    by_ref: list[TxCandidateDTO] | None = None,
    by_date: list[TxCandidateDTO] | None = None,
    by_inn: list[TxCandidateDTO] | None = None,
) -> MagicMock:
    f = MagicMock()
    f.find_by_external_ref.return_value = by_ref or []
    f.find_by_amount_and_date.return_value = by_date or []
    f.find_by_amount_and_inn.return_value = by_inn or []
    return f


# ── Strategy 1: exact doc_number ──────────────────────────────────────────────

@pytest.mark.django_db
class TestMatchingServiceStrategy1:
    def setup_method(self):
        self.tenant_id = _tenant()
        self.stmt = _make_statement(self.tenant_id)

    def test_single_hit_matched_100(self):
        tx = _dto()
        line = _make_line(self.stmt, self.tenant_id)
        service = MatchingService(_finder(by_ref=[tx]))
        counters = service.match_statement(self.stmt)

        refreshed = BankStatementLine.objects.get(id=line.id)
        assert refreshed.match_status == MatchStatus.MATCHED
        assert refreshed.match_confidence == 100
        assert refreshed.transaction_id == tx.transaction_id
        assert counters[MatchStatus.MATCHED] == 1

    def test_multi_hits_ambiguous_50(self):
        line = _make_line(self.stmt, self.tenant_id)
        service = MatchingService(_finder(by_ref=[_dto(), _dto()]))
        service.match_statement(self.stmt)

        refreshed = BankStatementLine.objects.get(id=line.id)
        assert refreshed.match_status == MatchStatus.AMBIGUOUS
        assert refreshed.match_confidence == 50
        assert refreshed.transaction_id is None


# ── Strategy 2: amount + date window ─────────────────────────────────────────

@pytest.mark.django_db
class TestMatchingServiceStrategy2:
    def setup_method(self):
        self.tenant_id = _tenant()
        self.stmt = _make_statement(self.tenant_id)

    def test_single_hit_matched_85(self):
        tx = _dto()
        # no doc_number so strategy 1 is skipped
        line = _make_line(self.stmt, self.tenant_id, doc_number="")
        service = MatchingService(_finder(by_date=[tx]))
        service.match_statement(self.stmt)

        refreshed = BankStatementLine.objects.get(id=line.id)
        assert refreshed.match_status == MatchStatus.MATCHED
        assert refreshed.match_confidence == 85
        assert refreshed.transaction_id == tx.transaction_id

    def test_multi_hits_ambiguous_50(self):
        line = _make_line(self.stmt, self.tenant_id, doc_number="")
        service = MatchingService(_finder(by_date=[_dto(), _dto()]))
        service.match_statement(self.stmt)

        refreshed = BankStatementLine.objects.get(id=line.id)
        assert refreshed.match_status == MatchStatus.AMBIGUOUS
        assert refreshed.match_confidence == 50

    def test_skipped_when_no_transaction_date(self):
        tx = _dto()
        line = _make_line(
            self.stmt, self.tenant_id,
            doc_number="",
            transaction_date=None,
        )
        finder = _finder(by_inn=[tx])
        service = MatchingService(finder)
        service.match_statement(self.stmt)

        finder.find_by_amount_and_date.assert_not_called()
        refreshed = BankStatementLine.objects.get(id=line.id)
        # falls through to strategy 3
        assert refreshed.match_status == MatchStatus.PARTIAL


# ── Strategy 3: amount + INN ──────────────────────────────────────────────────

@pytest.mark.django_db
class TestMatchingServiceStrategy3:
    def setup_method(self):
        self.tenant_id = _tenant()
        self.stmt = _make_statement(self.tenant_id)

    def test_single_hit_partial_60(self):
        tx = _dto()
        line = _make_line(
            self.stmt, self.tenant_id,
            doc_number="",
            transaction_date=None,
        )
        service = MatchingService(_finder(by_inn=[tx]))
        service.match_statement(self.stmt)

        refreshed = BankStatementLine.objects.get(id=line.id)
        assert refreshed.match_status == MatchStatus.PARTIAL
        assert refreshed.match_confidence == 60
        assert refreshed.transaction_id == tx.transaction_id

    def test_multi_hits_ambiguous_40(self):
        line = _make_line(
            self.stmt, self.tenant_id,
            doc_number="",
            transaction_date=None,
        )
        service = MatchingService(_finder(by_inn=[_dto(), _dto()]))
        service.match_statement(self.stmt)

        refreshed = BankStatementLine.objects.get(id=line.id)
        assert refreshed.match_status == MatchStatus.AMBIGUOUS
        assert refreshed.match_confidence == 40

    def test_skipped_when_no_inn(self):
        line = _make_line(
            self.stmt, self.tenant_id,
            doc_number="",
            transaction_date=None,
            counterparty_inn="",
        )
        finder = _finder()
        service = MatchingService(finder)
        service.match_statement(self.stmt)

        finder.find_by_amount_and_inn.assert_not_called()
        refreshed = BankStatementLine.objects.get(id=line.id)
        assert refreshed.match_status == MatchStatus.UNMATCHED


# ── No signal → UNMATCHED ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestMatchingServiceUnmatched:
    def setup_method(self):
        self.tenant_id = _tenant()
        self.stmt = _make_statement(self.tenant_id)

    def test_no_signal_unmatched_0(self):
        line = _make_line(
            self.stmt, self.tenant_id,
            doc_number="",
            transaction_date=None,
            counterparty_inn="",
        )
        service = MatchingService(_finder())
        counters = service.match_statement(self.stmt)

        refreshed = BankStatementLine.objects.get(id=line.id)
        assert refreshed.match_status == MatchStatus.UNMATCHED
        assert refreshed.match_confidence == 0
        assert refreshed.transaction_id is None
        assert counters[MatchStatus.UNMATCHED] == 1

    def test_none_amount_unmatched(self):
        line = _make_line(self.stmt, self.tenant_id, amount=None)
        service = MatchingService(_finder())
        service.match_statement(self.stmt)

        refreshed = BankStatementLine.objects.get(id=line.id)
        assert refreshed.match_status == MatchStatus.UNMATCHED
        assert refreshed.match_confidence == 0


# ── Precedence ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestMatchingServicePrecedence:
    def setup_method(self):
        self.tenant_id = _tenant()
        self.stmt = _make_statement(self.tenant_id)

    def test_strategy1_wins_when_all_strategies_return_results(self):
        tx_ref = _dto()
        tx_date = _dto()
        tx_inn = _dto()
        line = _make_line(self.stmt, self.tenant_id)
        service = MatchingService(_finder(
            by_ref=[tx_ref],
            by_date=[tx_date],
            by_inn=[tx_inn],
        ))
        service.match_statement(self.stmt)

        refreshed = BankStatementLine.objects.get(id=line.id)
        assert refreshed.match_confidence == 100
        assert refreshed.transaction_id == tx_ref.transaction_id

    def test_strategy2_used_when_strategy1_empty(self):
        tx_date = _dto()
        line = _make_line(self.stmt, self.tenant_id)
        finder = _finder(by_ref=[], by_date=[tx_date])
        service = MatchingService(finder)
        service.match_statement(self.stmt)

        refreshed = BankStatementLine.objects.get(id=line.id)
        assert refreshed.match_confidence == 85
        assert refreshed.transaction_id == tx_date.transaction_id

    def test_strategy1_stops_on_ambiguous_does_not_fall_to_strategy2(self):
        """Multiple doc_number hits → AMBIGUOUS(50), strategy 2 not called."""
        line = _make_line(self.stmt, self.tenant_id)
        finder = _finder(by_ref=[_dto(), _dto()], by_date=[_dto()])
        service = MatchingService(finder)
        service.match_statement(self.stmt)

        refreshed = BankStatementLine.objects.get(id=line.id)
        assert refreshed.match_status == MatchStatus.AMBIGUOUS
        assert refreshed.match_confidence == 50
        finder.find_by_amount_and_date.assert_not_called()


# ── Counters ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestMatchingServiceCounters:
    def setup_method(self):
        self.tenant_id = _tenant()
        self.stmt = _make_statement(self.tenant_id)

    def test_counters_sum_to_line_count(self):
        tx = _dto()
        # line 1: MATCHED via doc_number
        BankStatementLine.objects.create(
            tenant_id=self.tenant_id,
            statement=self.stmt,
            line_number=1,
            parse_status="parsed",
            amount=Decimal("50000.00"),
            doc_number="PAY-001",
        )
        # line 2: UNMATCHED (no signals)
        BankStatementLine.objects.create(
            tenant_id=self.tenant_id,
            statement=self.stmt,
            line_number=2,
            parse_status="parsed",
            amount=Decimal("10000.00"),
        )
        # line 3: failed parse — skipped by match_statement
        BankStatementLine.objects.create(
            tenant_id=self.tenant_id,
            statement=self.stmt,
            line_number=3,
            parse_status="failed",
            amount=Decimal("1000.00"),
        )

        def ref_side_effect(tenant_id, external_ref):
            if external_ref == "PAY-001":
                return [tx]
            return []

        finder = MagicMock()
        finder.find_by_external_ref.side_effect = ref_side_effect
        finder.find_by_amount_and_date.return_value = []
        finder.find_by_amount_and_inn.return_value = []

        counters = MatchingService(finder).match_statement(self.stmt)
        assert counters[MatchStatus.MATCHED] == 1
        assert counters[MatchStatus.UNMATCHED] == 1
        assert counters[MatchStatus.PARTIAL] == 0
        assert counters[MatchStatus.AMBIGUOUS] == 0
