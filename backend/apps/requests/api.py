import uuid
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest
from ninja import Query, Router

from apps.common.api.errors import make_error_response
from apps.common.api.permissions import require_permission
from apps.objects.contracts.object_creator_impl import DjangoObjectCreator
from apps.requests.queries.request_queries import get_request_detail, get_requests_page
from apps.requests.schemas.request_dto import (
    ConvertResponse,
    RequestAssignRequest,
    RequestCreateRequest,
    RequestListResponse,
    RequestResponse,
)
from apps.requests.services.request_create_service import RequestCreateService
from apps.requests.services.request_workflow_service import RequestWorkflowService
from apps.users.contracts.user_info_impl import DjangoUserInfoProvider

router = Router(tags=["requests"])

_object_creator = DjangoObjectCreator()
_user_info = DjangoUserInfoProvider()


@router.get("/", response={200: RequestListResponse, 403: dict})
@require_permission("read", "requests")
def list_requests(
    request: HttpRequest,
    cursor: Optional[uuid.UUID] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    manager_id: Optional[uuid.UUID] = Query(None),
):
    result = get_requests_page(
        cursor=cursor,
        limit=limit,
        status=status,
        resource_type=resource_type,
        manager_id=manager_id,
        user_info=_user_info,
    )
    return 200, result


@router.post("/", response={201: RequestResponse, 403: dict, 422: dict})
@require_permission("write", "requests")
def create_request(request: HttpRequest, body: RequestCreateRequest):
    try:
        req = RequestCreateService.execute(
            client_name=body.client_name,
            client_phone=body.client_phone,
            client_email=body.client_email,
            address=body.address,
            resource_type=body.resource_type,
            comment=body.comment,
            source=body.source,
            manager_id=body.manager_id,
        )
    except PermissionDenied:
        return 403, make_error_response("FORBIDDEN", "Нет доступа.")
    return 201, get_request_detail(req.id, user_info=_user_info)


@router.get("/{request_id}/", response={200: RequestResponse, 403: dict, 404: dict})
@require_permission("read", "requests")
def get_request(request: HttpRequest, request_id: uuid.UUID):
    try:
        return 200, get_request_detail(request_id, user_info=_user_info)
    except PermissionDenied:
        return 404, make_error_response("REQUEST_NOT_FOUND", "Заявка не найдена.")


@router.post("/{request_id}/assign/", response={200: RequestResponse, 403: dict, 422: dict})
@require_permission("write", "requests")
def assign_request(request: HttpRequest, request_id: uuid.UUID, body: RequestAssignRequest):
    try:
        RequestWorkflowService.assign(request_id, body.manager_id)
        return 200, get_request_detail(request_id, user_info=_user_info)
    except PermissionDenied:
        return 403, make_error_response("FORBIDDEN", "Нет доступа.")
    except ValidationError as e:
        return 422, make_error_response("INVALID_TRANSITION", str(e.message))


@router.post("/{request_id}/process/", response={200: RequestResponse, 403: dict, 422: dict})
@require_permission("write", "requests")
def process_request(request: HttpRequest, request_id: uuid.UUID):
    try:
        RequestWorkflowService.start_processing(request_id)
        return 200, get_request_detail(request_id, user_info=_user_info)
    except PermissionDenied:
        return 403, make_error_response("FORBIDDEN", "Нет доступа.")
    except ValidationError as e:
        return 422, make_error_response("INVALID_TRANSITION", str(e.message))


@router.post("/{request_id}/convert/", response={200: ConvertResponse, 403: dict, 422: dict})
@require_permission("write", "requests")
def convert_request(request: HttpRequest, request_id: uuid.UUID):
    try:
        object_id = RequestWorkflowService.convert(request_id, _object_creator)
        return 200, ConvertResponse(object_id=object_id)
    except PermissionDenied:
        return 403, make_error_response("FORBIDDEN", "Нет доступа.")
    except ValidationError as e:
        return 422, make_error_response("INVALID_TRANSITION", str(e.message))


@router.post("/{request_id}/close/", response={200: RequestResponse, 403: dict, 422: dict})
@require_permission("write", "requests")
def close_request(request: HttpRequest, request_id: uuid.UUID):
    try:
        RequestWorkflowService.close(request_id)
        return 200, get_request_detail(request_id, user_info=_user_info)
    except PermissionDenied:
        return 403, make_error_response("FORBIDDEN", "Нет доступа.")
    except ValidationError as e:
        return 422, make_error_response("INVALID_TRANSITION", str(e.message))
