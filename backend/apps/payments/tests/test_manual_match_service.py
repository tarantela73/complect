"""
Tests for ManualMatchService:
  - attach: happy path, outbox event, previous_status, all source statuses
  - attach: anti-IDOR guard (foreign-tenant transaction_id → PermissionDenied)
  - detach: happy path, outbox event, idempotent on UNMATCHED
  - get_candidates: aggregation, deduplication, no-amount guard
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import PermissionDenied

from apps.common.models.outbox import OutboxRecord
from apps.common.tenancy.context import set_tenant_context
from apps.common.contracts.finance_contracts import TxCandidateDTO
from apps.payments.events import BankStatementLineMatchedEvent
from apps.payments.models.bank_statement import BankStatement, BankStatementStatus
from apps.payments.models.bank_statement_line import BankStatementLine, MatchStatus
from apps.payments.services.manual_match_service import ManualMatchService


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_tenant() -> uuid.UUID:
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
    match_status: str = MatchStatus.UNMATCHED,
    match_confidence: int = 0,
    transaction_id: uuid.UUID | None = None,
    amount: str = "50000.00",
    doc_number: str = "",
    counterparty_inn: str = "",
    transaction_date: date | None = None,
) -> BankStatementLine:
    return BankStatementLine.objects.create(
        tenant_id=tenant_id,
        statement=statement,
        line_number=1,
        parse_status="parsed",
        amount=Decimal(amount),
        match_status=match_status,
        match_confidence=match_confidence,
        transaction_id=transaction_id,
        doc_number=doc_number,
        counterparty_inn=counterparty_inn,
        transaction_date=transaction_date,
    )


def _make_tx_dto(
    tenant_id: uuid.UUID,
    amount_kopecks: int = 5_000_000,
) -> TxCandidateDTO:
    return TxCandidateDTO(
        transaction_id=uuid.uuid4(),
        contract_id=uuid.uuid4(),
        amount_kopecks=amount_kopecks,
        external_ref="PAY-001",
        counterparty_inn="7700000001",
        value_date=date(2025, 1, 15),
    )


def _mock_finder(
    *,
    return_dto: TxCandidateDTO | None = None,
    find_by_id_result: TxCandidateDTO | None = None,
) -> MagicMock:
    finder = MagicMock()
    finder.find_by_id.return_value = find_by_id_result
    finder.find_by_external_ref.return_value = [return_dto] if return_dto else []
    finder.find_by_amount_and_date.return_value = [return_dto] if return_dto else []
    finder.find_by_amount_and_inn.return_value = [return_dto] if return_dto else []
    return finder


# ── attach ─────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestManualMatchServiceAttach:
    def setup_method(self):
        self.tenant_id = _make_tenant()
        self.actor_id = uuid.uuid4()
        self.stmt = _make_statement(self.tenant_id)
        self.tx_dto = _make_tx_dto(self.tenant_id)

    def _patch_finder(self, dto: TxCandidateDTO | None = None):
        import apps.payments.services.manual_match_service as svc_module
        mock = _mock_finder(find_by_id_result=dto)
        self._orig = getattr(
            __import__(
                "apps.finance.services.transaction_finder",
                fromlist=["get_transaction_finder"],
            ),
            "get_transaction_finder",
            None,
        )
        svc_module._patched_finder = mock
        return mock

    def test_attach_sets_matched_status(self, monkeypatch):
        line = _make_line(self.stmt, self.tenant_id)
        monkeypatch.setattr(
            "apps.finance.services.transaction_finder.get_transaction_finder",
            lambda: _mock_finder(find_by_id_result=self.tx_dto),
        )
        result = ManualMatchService.attach(
            tenant_id=self.tenant_id,
            line=line,
            transaction_id=self.tx_dto.transaction_id,
            confirmed_by=self.actor_id,
        )
        assert result.match_status == MatchStatus.MATCHED
        assert result.match_confidence == 100
        assert result.transaction_id == self.tx_dto.transaction_id

    def test_attach_persists_to_db(self, monkeypatch):
        line = _make_line(self.stmt, self.tenant_id)
        monkeypatch.setattr(
            "apps.finance.services.transaction_finder.get_transaction_finder",
            lambda: _mock_finder(find_by_id_result=self.tx_dto),
        )
        ManualMatchService.attach(
            tenant_id=self.tenant_id,
            line=line,
            transaction_id=self.tx_dto.transaction_id,
            confirmed_by=self.actor_id,
        )
        refreshed = BankStatementLine.objects.get(id=line.id)
        assert refreshed.match_status == MatchStatus.MATCHED
        assert refreshed.match_confidence == 100
        assert refreshed.transaction_id == self.tx_dto.transaction_id

    def test_attach_writes_outbox_event(self, monkeypatch):
        line = _make_line(self.stmt, self.tenant_id)
        monkeypatch.setattr(
            "apps.finance.services.transaction_finder.get_transaction_finder",
            lambda: _mock_finder(find_by_id_result=self.tx_dto),
        )
        ManualMatchService.attach(
            tenant_id=self.tenant_id,
            line=line,
            transaction_id=self.tx_dto.transaction_id,
            confirmed_by=self.actor_id,
        )
        record = OutboxRecord.objects.filter(
            event_type=BankStatementLineMatchedEvent.EVENT_TYPE,
        ).last()
        assert record is not None
        assert record.payload_json["tenant_id"] == str(self.tenant_id)
        assert record.payload_json["transaction_id"] == str(self.tx_dto.transaction_id)
        assert record.payload_json["match_status"] == MatchStatus.MATCHED
        assert record.payload_json["match_confidence"] == 100

    def test_attach_records_previous_status(self, monkeypatch):
        line = _make_line(
            self.stmt, self.tenant_id,
            match_status=MatchStatus.PARTIAL,
            match_confidence=60,
        )
        monkeypatch.setattr(
            "apps.finance.services.transaction_finder.get_transaction_finder",
            lambda: _mock_finder(find_by_id_result=self.tx_dto),
        )
        ManualMatchService.attach(
            tenant_id=self.tenant_id,
            line=line,
            transaction_id=self.tx_dto.transaction_id,
            confirmed_by=self.actor_id,
        )
        record = OutboxRecord.objects.filter(
            event_type=BankStatementLineMatchedEvent.EVENT_TYPE,
        ).last()
        assert record.payload_json["previous_status"] == MatchStatus.PARTIAL

    @pytest.mark.parametrize("source_status", [
        MatchStatus.UNMATCHED,
        MatchStatus.PARTIAL,
        MatchStatus.AMBIGUOUS,
        MatchStatus.MATCHED,
    ])
    def test_attach_allowed_from_any_status(self, monkeypatch, source_status):
        line = _make_line(self.stmt, self.tenant_id, match_status=source_status)
        monkeypatch.setattr(
            "apps.finance.services.transaction_finder.get_transaction_finder",
            lambda: _mock_finder(find_by_id_result=self.tx_dto),
        )
        result = ManualMatchService.attach(
            tenant_id=self.tenant_id,
            line=line,
            transaction_id=self.tx_dto.transaction_id,
            confirmed_by=self.actor_id,
        )
        assert result.match_status == MatchStatus.MATCHED

    def test_attach_foreign_tenant_raises_permission_denied(self, monkeypatch):
        """Anti-IDOR guard: find_by_id returns None for foreign-tenant UUID → 403."""
        line = _make_line(self.stmt, self.tenant_id)
        foreign_tx_id = uuid.uuid4()
        monkeypatch.setattr(
            "apps.finance.services.transaction_finder.get_transaction_finder",
            lambda: _mock_finder(find_by_id_result=None),
        )
        with pytest.raises(PermissionDenied):
            ManualMatchService.attach(
                tenant_id=self.tenant_id,
                line=line,
                transaction_id=foreign_tx_id,
                confirmed_by=self.actor_id,
            )

    def test_attach_foreign_tenant_does_not_mutate_db(self, monkeypatch):
        line = _make_line(self.stmt, self.tenant_id)
        monkeypatch.setattr(
            "apps.finance.services.transaction_finder.get_transaction_finder",
            lambda: _mock_finder(find_by_id_result=None),
        )
        with pytest.raises(PermissionDenied):
            ManualMatchService.attach(
                tenant_id=self.tenant_id,
                line=line,
                transaction_id=uuid.uuid4(),
                confirmed_by=self.actor_id,
            )
        refreshed = BankStatementLine.objects.get(id=line.id)
        assert refreshed.match_status == MatchStatus.UNMATCHED
        assert refreshed.transaction_id is None


# ── detach ─────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestManualMatchServiceDetach:
    def setup_method(self):
        self.tenant_id = _make_tenant()
        self.actor_id = uuid.uuid4()
        self.stmt = _make_statement(self.tenant_id)
        self.tx_id = uuid.uuid4()

    def test_detach_resets_to_unmatched(self):
        line = _make_line(
            self.stmt, self.tenant_id,
            match_status=MatchStatus.MATCHED,
            match_confidence=100,
            transaction_id=self.tx_id,
        )
        result = ManualMatchService.detach(
            tenant_id=self.tenant_id,
            line=line,
            confirmed_by=self.actor_id,
        )
        assert result.match_status == MatchStatus.UNMATCHED
        assert result.match_confidence == 0
        assert result.transaction_id is None

    def test_detach_persists_to_db(self):
        line = _make_line(
            self.stmt, self.tenant_id,
            match_status=MatchStatus.MATCHED,
            match_confidence=100,
            transaction_id=self.tx_id,
        )
        ManualMatchService.detach(
            tenant_id=self.tenant_id,
            line=line,
            confirmed_by=self.actor_id,
        )
        refreshed = BankStatementLine.objects.get(id=line.id)
        assert refreshed.match_status == MatchStatus.UNMATCHED
        assert refreshed.transaction_id is None

    def test_detach_writes_outbox_event(self):
        line = _make_line(
            self.stmt, self.tenant_id,
            match_status=MatchStatus.MATCHED,
            match_confidence=85,
            transaction_id=self.tx_id,
        )
        before_count = OutboxRecord.objects.filter(
            event_type=BankStatementLineMatchedEvent.EVENT_TYPE
        ).count()
        ManualMatchService.detach(
            tenant_id=self.tenant_id,
            line=line,
            confirmed_by=self.actor_id,
        )
        after_count = OutboxRecord.objects.filter(
            event_type=BankStatementLineMatchedEvent.EVENT_TYPE
        ).count()
        assert after_count == before_count + 1

    def test_detach_outbox_has_null_transaction_id(self):
        line = _make_line(
            self.stmt, self.tenant_id,
            match_status=MatchStatus.MATCHED,
            match_confidence=100,
            transaction_id=self.tx_id,
        )
        ManualMatchService.detach(
            tenant_id=self.tenant_id,
            line=line,
            confirmed_by=self.actor_id,
        )
        record = OutboxRecord.objects.filter(
            event_type=BankStatementLineMatchedEvent.EVENT_TYPE,
        ).last()
        assert record.payload_json["transaction_id"] is None
        assert record.payload_json["previous_status"] == MatchStatus.MATCHED

    def test_detach_on_unmatched_is_idempotent(self):
        line = _make_line(self.stmt, self.tenant_id, match_status=MatchStatus.UNMATCHED)
        before_count = OutboxRecord.objects.filter(
            event_type=BankStatementLineMatchedEvent.EVENT_TYPE
        ).count()
        result = ManualMatchService.detach(
            tenant_id=self.tenant_id,
            line=line,
            confirmed_by=self.actor_id,
        )
        assert result.match_status == MatchStatus.UNMATCHED
        after_count = OutboxRecord.objects.filter(
            event_type=BankStatementLineMatchedEvent.EVENT_TYPE
        ).count()
        assert after_count == before_count  # no outbox event written


# ── get_candidates ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestManualMatchServiceGetCandidates:
    def setup_method(self):
        self.tenant_id = _make_tenant()
        self.stmt = _make_statement(self.tenant_id)

    def test_returns_empty_when_no_amount(self, monkeypatch):
        line = _make_line(self.stmt, self.tenant_id, amount="0.00")
        line.amount = None
        line.save(update_fields=["amount"])
        monkeypatch.setattr(
            "apps.finance.services.transaction_finder.get_transaction_finder",
            lambda: _mock_finder(),
        )
        result = ManualMatchService.get_candidates(tenant_id=self.tenant_id, line=line)
        assert result == []

    def test_deduplicates_across_strategies(self, monkeypatch):
        shared_dto = _make_tx_dto(self.tenant_id)
        finder = MagicMock()
        finder.find_by_external_ref.return_value = [shared_dto]
        finder.find_by_amount_and_date.return_value = [shared_dto]  # same UUID
        finder.find_by_amount_and_inn.return_value = []
        monkeypatch.setattr(
            "apps.finance.services.transaction_finder.get_transaction_finder",
            lambda: finder,
        )
        line = _make_line(
            self.stmt, self.tenant_id,
            doc_number="PAY-001",
            counterparty_inn="7700000001",
            transaction_date=date(2025, 1, 15),
        )
        result = ManualMatchService.get_candidates(tenant_id=self.tenant_id, line=line)
        assert len(result) == 1
        assert result[0].transaction_id == shared_dto.transaction_id

    def test_aggregates_distinct_candidates(self, monkeypatch):
        dto1 = _make_tx_dto(self.tenant_id)
        dto2 = _make_tx_dto(self.tenant_id)
        finder = MagicMock()
        finder.find_by_external_ref.return_value = [dto1]
        finder.find_by_amount_and_date.return_value = [dto2]
        finder.find_by_amount_and_inn.return_value = []
        monkeypatch.setattr(
            "apps.finance.services.transaction_finder.get_transaction_finder",
            lambda: finder,
        )
        line = _make_line(
            self.stmt, self.tenant_id,
            doc_number="PAY-001",
            transaction_date=date(2025, 1, 15),
        )
        result = ManualMatchService.get_candidates(tenant_id=self.tenant_id, line=line)
        ids = {c.transaction_id for c in result}
        assert dto1.transaction_id in ids
        assert dto2.transaction_id in ids

    def test_skips_doc_number_search_when_empty(self, monkeypatch):
        dto = _make_tx_dto(self.tenant_id)
        finder = MagicMock()
        finder.find_by_external_ref.return_value = [dto]
        finder.find_by_amount_and_date.return_value = []
        finder.find_by_amount_and_inn.return_value = []
        monkeypatch.setattr(
            "apps.finance.services.transaction_finder.get_transaction_finder",
            lambda: finder,
        )
        line = _make_line(self.stmt, self.tenant_id, doc_number="")
        ManualMatchService.get_candidates(tenant_id=self.tenant_id, line=line)
        finder.find_by_external_ref.assert_not_called()
