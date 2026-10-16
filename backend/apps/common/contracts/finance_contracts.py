from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class LastPaymentDTO:
    transaction_id: uuid.UUID
    amount: int
    value_date: date | None


@runtime_checkable
class FinanceReaderProtocol(Protocol):
    """[ADR-0005] Cross-domain read contract: apps/contracts → apps/finance (Query Side)."""

    def get_last_posted_payments(
        self,
        tenant_id: uuid.UUID,
        contract_id: uuid.UUID,
        limit: int = 5,
    ) -> list[LastPaymentDTO]: ...


_finance_reader: FinanceReaderProtocol | None = None


def register_finance_reader(reader: FinanceReaderProtocol) -> None:
    """Register the Finance-owned reader at the composition root."""
    global _finance_reader
    if _finance_reader is not None and _finance_reader is not reader:
        raise RuntimeError("Finance reader already registered")
    _finance_reader = reader


def get_finance_reader() -> FinanceReaderProtocol:
    if _finance_reader is None:
        raise RuntimeError("FINANCE_READER_UNAVAILABLE")
    return _finance_reader


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

    def find_by_id(
        self,
        tenant_id: uuid.UUID,
        transaction_id: uuid.UUID,
    ) -> TxCandidateDTO | None:
        """Точечная проверка принадлежности транзакции тенанту (anti-IDOR guard)."""
        ...
