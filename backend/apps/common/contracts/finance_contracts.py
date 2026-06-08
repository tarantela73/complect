from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class TxCandidateDTO:
    transaction_id: uuid.UUID
    contract_id: uuid.UUID
    amount_kopecks: int
    external_ref: str
    counterparty_inn: str
    value_date: date | None


@runtime_checkable
class FinanceTransactionFinder(Protocol):
    """
    [ADR-0005] Cross-domain контракт: apps/payments → apps/finance.
    Реализацию предоставляет apps.finance; инжектируется в MatchingService.
    """

    def find_by_external_ref(
        self,
        tenant_id: uuid.UUID,
        external_ref: str,
    ) -> list[TxCandidateDTO]:
        """Точный поиск по document/payment reference (наибольший confidence)."""
        ...

    def find_by_amount_and_date(
        self,
        tenant_id: uuid.UUID,
        amount_kopecks: int,
        date_from: date,
        date_to: date,
    ) -> list[TxCandidateDTO]:
        """Поиск PENDING/POSTED транзакций по сумме в датовом окне."""
        ...

    def find_by_amount_and_inn(
        self,
        tenant_id: uuid.UUID,
        amount_kopecks: int,
        counterparty_inn: str,
    ) -> list[TxCandidateDTO]:
        """Поиск по сумме + ИНН контрагента."""
        ...
