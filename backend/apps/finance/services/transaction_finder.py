from __future__ import annotations

import uuid
from datetime import date

from apps.common.contracts.finance_contracts import FinanceTransactionFinder, TxCandidateDTO
from apps.finance.models.transaction import Transaction, TransactionStatus

_MATCHABLE_STATUSES = (TransactionStatus.PENDING, TransactionStatus.POSTED)


def _to_dto(tx: Transaction) -> TxCandidateDTO:
    return TxCandidateDTO(
        transaction_id=tx.id,
        contract_id=tx.contract_id,
        amount_kopecks=tx.amount,
        external_ref=tx.external_ref,
        counterparty_inn=tx.counterparty_inn,
        value_date=tx.value_date,
    )


def _qs(tenant_id: uuid.UUID):
    return Transaction.objects.filter(
        tenant_id=tenant_id,
        is_deleted=False,
        status__in=_MATCHABLE_STATUSES,
    )


class DjangoTransactionFinder:
    """Implements FinanceTransactionFinder over the Django ORM."""

    def find_by_external_ref(
        self,
        tenant_id: uuid.UUID,
        external_ref: str,
    ) -> list[TxCandidateDTO]:
        if not external_ref:
            return []
        return [_to_dto(tx) for tx in _qs(tenant_id).filter(external_ref=external_ref)]

    def find_by_amount_and_date(
        self,
        tenant_id: uuid.UUID,
        amount_kopecks: int,
        date_from: date,
        date_to: date,
    ) -> list[TxCandidateDTO]:
        return [
            _to_dto(tx)
            for tx in _qs(tenant_id).filter(
                amount=amount_kopecks,
                value_date__gte=date_from,
                value_date__lte=date_to,
            )
        ]

    def find_by_amount_and_inn(
        self,
        tenant_id: uuid.UUID,
        amount_kopecks: int,
        counterparty_inn: str,
    ) -> list[TxCandidateDTO]:
        if not counterparty_inn:
            return []
        return [
            _to_dto(tx)
            for tx in _qs(tenant_id).filter(
                amount=amount_kopecks,
                counterparty_inn=counterparty_inn,
            )
        ]


# Singleton — инжектируется в MatchingService как default
_finder: FinanceTransactionFinder = DjangoTransactionFinder()


def get_transaction_finder() -> FinanceTransactionFinder:
    return _finder
