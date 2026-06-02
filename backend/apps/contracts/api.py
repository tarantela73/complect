import uuid
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest
from ninja import Query, Router

from apps.common.api.errors import make_error_response
from apps.contracts.queries.contract_queries import get_contract_detail, get_contracts_page
from apps.contracts.schemas.contract_dto import (
    ContractCreateRequestSchema,
    ContractListResponse,
    ContractResponseSchema,
)
from apps.contracts.services.contract_closure import ContractClosureService
from apps.contracts.services.contract_create_service import ContractCreateService

router = Router(tags=["contracts"])


@router.get("/", response={200: ContractListResponse})
def list_contracts(
    request: HttpRequest,
    cursor: Optional[uuid.UUID] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
):
    result = get_contracts_page(cursor=cursor, limit=limit, status=status, resource_type=resource_type)
    return 200, result


@router.get("/{contract_id}/", response={200: ContractResponseSchema, 403: dict})
def get_contract(request: HttpRequest, contract_id: uuid.UUID):
    try:
        return 200, get_contract_detail(contract_id)
    except PermissionDenied:
        return 403, make_error_response("CONTRACT_NOT_FOUND", "Договор не найден.")


@router.post("/", response={201: ContractResponseSchema, 400: dict, 409: dict})
def create_contract(request: HttpRequest, body: ContractCreateRequestSchema):
    try:
        result = ContractCreateService.execute(body)
        return 201, result
    except ValidationError as exc:
        return 400, make_error_response("VALIDATION_ERROR", str(exc))


@router.post("/{contract_id}/close/", response={200: ContractResponseSchema, 400: dict, 403: dict, 409: dict})
def close_contract(request: HttpRequest, contract_id: uuid.UUID):
    from apps.common.tenancy.context_core import get_context

    try:
        ctx = get_context()
        ContractClosureService.execute_contract_closure(
            contract_id=contract_id,
            user_id=ctx.user_id,
            correlation_id=ctx.correlation_id,
        )
        return 200, get_contract_detail(contract_id)
    except PermissionDenied:
        return 403, make_error_response("CONTRACT_NOT_FOUND", "Договор не найден.")
    except ValidationError as exc:
        return 400, make_error_response("BUSINESS_RULE_VIOLATION", str(exc))
