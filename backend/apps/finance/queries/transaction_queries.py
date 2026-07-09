"""CQRS Query Side — read-only, cursor pagination, no writes."""

import uuid
from typing import Optional

from apps.common.tenancy.context_core import get_tenant_id
from apps.finance.models.transaction import Transaction
from apps.finance.schemas.transaction_dto import TransactionResponse


def _tx_qs():
    tenant_id = get_tenant_id()
    return Transaction.objects.filter(tenant_id=tenant_id, is_deleted=False)


def get_transactions_page(
    cursor: Optional[uuid.UUID] = None,
    limit: int = 20,
    contract_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    transaction_type: Optional[str] = None,
) -> dict:
    qs = _tx_qs().order_by("-id")

    if cursor:
        qs = qs.filter(id__lt=cursor)
    if contract_id:
        qs = qs.filter(contract_id=contract_id)
    if status:
        qs = qs.filter(status=status)
    if transaction_type:
        qs = qs.filter(transaction_type=transaction_type)

    rows = list(qs[: limit + 1])
    has_next = len(rows) > limit
    rows = rows[:limit]

    return {
        "results": [_to_dto(tx) for tx in rows],
        "next_cursor": str(rows[-1].id) if has_next and rows else None,
        "count": len(rows),
    }


def _to_dto(tx: Transaction) -> TransactionResponse:
    return TransactionResponse(
        id=tx.id,
        contract_id=tx.contract_id,
        transaction_type=tx.transaction_type,
        status=tx.status,
        amount=tx.amount,
        external_ref=tx.external_ref,
    )
