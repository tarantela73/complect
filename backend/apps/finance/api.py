import uuid
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest
from ninja import Query, Router

from apps.common.api.errors import make_error_response
from apps.common.tenancy.context_core import get_context
from apps.finance.queries.transaction_queries import get_transactions_page
from apps.finance.schemas.transaction_dto import (
    TransactionListResponse,
    TransactionPostRequest,
    TransactionResponse,
)
from apps.finance.services.transaction_posting import TransactionPostingService

router = Router(tags=["finance"])


@router.get("/transactions/", response={200: TransactionListResponse})
def list_transactions(
    request: HttpRequest,
    cursor: Optional[uuid.UUID] = Query(None),  # noqa: B008
    limit: int = Query(20, ge=1, le=100),  # noqa: B008
    contract_id: Optional[uuid.UUID] = Query(None),  # noqa: B008
    status: Optional[str] = Query(None),  # noqa: B008
    transaction_type: Optional[str] = Query(None),  # noqa: B008
):
    result = get_transactions_page(
        cursor=cursor,
        limit=limit,
        contract_id=contract_id,
        status=status,
        transaction_type=transaction_type,
    )
    return 200, result


@router.post("/transactions/", response={201: TransactionResponse, 400: dict, 403: dict})
def post_transaction(request: HttpRequest, body: TransactionPostRequest):
    try:
        ctx = get_context()
        tx = TransactionPostingService.post_payment(
            contract_id=body.contract_id,
            amount=body.amount,
            posted_by=ctx.user_id,
            external_ref=body.external_ref,
            transaction_type=body.transaction_type,
        )
        return 201, TransactionResponse(
            id=tx.id,
            contract_id=tx.contract_id,
            transaction_type=tx.transaction_type,
            status=tx.status,
            amount=tx.amount,
            external_ref=tx.external_ref,
        )
    except ValidationError as exc:
        return 400, make_error_response("VALIDATION_ERROR", str(exc))
    except PermissionDenied as exc:
        return 403, make_error_response("FORBIDDEN", str(exc))


@router.post(
    "/transactions/{tx_id}/reverse/",
    response={200: TransactionResponse, 403: dict},
)
def reverse_transaction(request: HttpRequest, tx_id: uuid.UUID):
    try:
        ctx = get_context()
        tx = TransactionPostingService.reverse_transaction(tx_id=tx_id, posted_by=ctx.user_id)
        return 200, TransactionResponse(
            id=tx.id,
            contract_id=tx.contract_id,
            transaction_type=tx.transaction_type,
            status=tx.status,
            amount=tx.amount,
            external_ref=tx.external_ref,
        )
    except PermissionDenied as exc:
        return 403, make_error_response("FORBIDDEN", str(exc))
