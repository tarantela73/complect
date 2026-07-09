from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.common.context.core import get_correlation_id
from apps.common.contracts.finance_contracts import TxCandidateDTO
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.payments.events import BankStatementLineMatchedEvent
from apps.payments.models.bank_statement_line import BankStatementLine, MatchStatus

_DATE_WINDOW = timedelta(days=3)


class ManualMatchService:
    @staticmethod
    def attach(
        tenant_id: uuid.UUID,
        line: BankStatementLine,
        transaction_id: uuid.UUID,
        confirmed_by: uuid.UUID,
    ) -> BankStatementLine:
        from apps.finance.services.transaction_finder import get_transaction_finder  # noqa: PLC0415

        if get_transaction_finder().find_by_id(tenant_id, transaction_id) is None:
            raise PermissionDenied("TRANSACTION_NOT_IN_TENANT")

        previous_status = line.match_status
        with transaction.atomic():
            BankStatementLine.objects.filter(id=line.id, tenant_id=tenant_id).update(
                match_status=MatchStatus.MATCHED,
                match_confidence=100,
                transaction_id=transaction_id,
            )
            line.refresh_from_db()
            TransactionalOutboxService.push_to_outbox(
                BankStatementLineMatchedEvent.create(
                    correlation_id=_safe_correlation_id(),
                    tenant_id=tenant_id,
                    line_id=line.id,
                    statement_id=line.statement_id,
                    transaction_id=transaction_id,
                    match_status=MatchStatus.MATCHED,
                    match_confidence=100,
                    confirmed_by=confirmed_by,
                    previous_status=previous_status,
                )
            )
        return line

    @staticmethod
    def detach(
        tenant_id: uuid.UUID,
        line: BankStatementLine,
        confirmed_by: uuid.UUID,
    ) -> BankStatementLine:
        if line.match_status == MatchStatus.UNMATCHED:
            return line
        previous_status = line.match_status
        with transaction.atomic():
            BankStatementLine.objects.filter(id=line.id, tenant_id=tenant_id).update(
                match_status=MatchStatus.UNMATCHED,
                match_confidence=0,
                transaction_id=None,
            )
            line.refresh_from_db()
            TransactionalOutboxService.push_to_outbox(
                BankStatementLineMatchedEvent.create(
                    correlation_id=_safe_correlation_id(),
                    tenant_id=tenant_id,
                    line_id=line.id,
                    statement_id=line.statement_id,
                    transaction_id=None,
                    match_status=MatchStatus.UNMATCHED,
                    match_confidence=0,
                    confirmed_by=confirmed_by,
                    previous_status=previous_status,
                )
            )
        return line

    @staticmethod
    def get_candidates(
        tenant_id: uuid.UUID,
        line: BankStatementLine,
    ) -> list[TxCandidateDTO]:
        from apps.finance.services.transaction_finder import get_transaction_finder  # noqa: PLC0415

        amount_kopecks = _to_kopecks(line.amount)
        if amount_kopecks is None:
            return []

        finder = get_transaction_finder()
        seen: set[uuid.UUID] = set()
        result: list[TxCandidateDTO] = []

        def _add(hits: list[TxCandidateDTO]) -> None:
            for h in hits:
                if h.transaction_id not in seen:
                    seen.add(h.transaction_id)
                    result.append(h)

        if line.doc_number:
            _add(finder.find_by_external_ref(tenant_id, line.doc_number))
        if line.transaction_date:
            _add(
                finder.find_by_amount_and_date(
                    tenant_id,
                    amount_kopecks,
                    date_from=line.transaction_date - _DATE_WINDOW,
                    date_to=line.transaction_date + _DATE_WINDOW,
                )
            )
        if line.counterparty_inn:
            _add(finder.find_by_amount_and_inn(tenant_id, amount_kopecks, line.counterparty_inn))

        return result


def _to_kopecks(amount: Decimal | None) -> int | None:
    if amount is None:
        return None
    return int(amount * 100)


def _safe_correlation_id() -> uuid.UUID:
    try:
        return get_correlation_id()
    except Exception:  # noqa: BLE001
        return uuid.uuid4()
