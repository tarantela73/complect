"""
Reconciliation: сопоставляет строки банковской выписки с Finance Transactions.

Алгоритм (в порядке убывания confidence):
  1. Exact doc_number → external_ref     → MATCHED,   confidence=100
  2. Amount + date window (±3 дня)        → 1 result:  MATCHED 85 / >1: AMBIGUOUS 50
  3. Amount + INN                         → 1 result:  PARTIAL 60 / >1: AMBIGUOUS 40
  4. No signal                            → UNMATCHED, confidence=0

Не импортирует никакие модели из apps.finance — только Protocol.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from apps.common.contracts.finance_contracts import FinanceTransactionFinder
from apps.payments.models.bank_statement import BankStatement
from apps.payments.models.bank_statement_line import BankStatementLine, MatchStatus

_DATE_WINDOW = timedelta(days=3)


class MatchingService:
    def __init__(self, finder: FinanceTransactionFinder) -> None:
        self._finder = finder

    def match_statement(self, stmt: BankStatement) -> dict[str, int]:
        """
        Сопоставляет все PARSED строки выписки.
        Возвращает счётчики: {MATCHED, PARTIAL, AMBIGUOUS, UNMATCHED}.
        """
        lines = list(
            stmt.lines.filter(parse_status="parsed").select_for_update(skip_locked=True)
        )
        counters: dict[str, int] = {
            MatchStatus.MATCHED: 0,
            MatchStatus.PARTIAL: 0,
            MatchStatus.AMBIGUOUS: 0,
            MatchStatus.UNMATCHED: 0,
        }

        updates: list[BankStatementLine] = []
        for line in lines:
            status, confidence, tx_id = self._match_line(line, stmt.tenant_id)
            line.match_status = status
            line.match_confidence = confidence
            if tx_id is not None:
                line.transaction_id = tx_id
            updates.append(line)
            counters[status] += 1

        if updates:
            BankStatementLine.objects.bulk_update(
                updates,
                fields=["match_status", "match_confidence", "transaction_id"],
                batch_size=200,
            )

        return counters

    def _match_line(
        self,
        line: BankStatementLine,
        tenant_id: uuid.UUID,
    ) -> tuple[str, int, uuid.UUID | None]:
        amount_kopecks = _to_kopecks(line.amount)
        if amount_kopecks is None:
            return MatchStatus.UNMATCHED, 0, None

        # 1. Exact external_ref match
        if line.doc_number:
            hits = self._finder.find_by_external_ref(tenant_id, line.doc_number)
            if len(hits) == 1:
                return MatchStatus.MATCHED, 100, hits[0].transaction_id
            if len(hits) > 1:
                return MatchStatus.AMBIGUOUS, 50, None

        # 2. Amount + date window
        if line.transaction_date:
            hits = self._finder.find_by_amount_and_date(
                tenant_id,
                amount_kopecks,
                date_from=line.transaction_date - _DATE_WINDOW,
                date_to=line.transaction_date + _DATE_WINDOW,
            )
            if len(hits) == 1:
                return MatchStatus.MATCHED, 85, hits[0].transaction_id
            if len(hits) > 1:
                return MatchStatus.AMBIGUOUS, 50, None

        # 3. Amount + INN
        if line.counterparty_inn:
            hits = self._finder.find_by_amount_and_inn(
                tenant_id,
                amount_kopecks,
                line.counterparty_inn,
            )
            if len(hits) == 1:
                return MatchStatus.PARTIAL, 60, hits[0].transaction_id
            if len(hits) > 1:
                return MatchStatus.AMBIGUOUS, 40, None

        return MatchStatus.UNMATCHED, 0, None


def _to_kopecks(amount: Decimal | None) -> int | None:
    if amount is None:
        return None
    return int(amount * 100)
