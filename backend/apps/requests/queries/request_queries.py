import uuid
from typing import Optional

from apps.common.contracts.user_info_contracts import UserInfoProtocol
from apps.common.tenancy.context_core import get_tenant_id
from apps.requests.models.request import Request
from apps.requests.schemas.request_dto import RequestListResponse, RequestResponse


def _to_response(
    req: Request,
    display_map: Optional[dict] = None,
) -> RequestResponse:
    manager_name: Optional[str] = None
    if display_map and req.manager_id and req.manager_id in display_map:
        manager_name = display_map[req.manager_id].full_name
    return RequestResponse(
        id=req.id,
        client_name=req.client_name,
        client_phone=req.client_phone,
        client_email=req.client_email,
        address=req.address,
        resource_type=req.resource_type,
        comment=req.comment,
        source=req.source,
        status=req.status,
        manager_id=req.manager_id,
        manager_name=manager_name,
        converted_object_id=req.converted_object_id,
        converted_at=req.converted_at,
        created_at=req.created_at,
    )


def _build_display_map(
    rows: list[Request],
    tenant_id: uuid.UUID,
    user_info: Optional[UserInfoProtocol],
) -> dict:
    if not user_info or not rows:
        return {}
    manager_ids = [r.manager_id for r in rows if r.manager_id]
    if not manager_ids:
        return {}
    return user_info.get_users_display(manager_ids, tenant_id)


def get_requests_page(
    cursor: Optional[uuid.UUID] = None,
    limit: int = 20,
    status: Optional[str] = None,
    resource_type: Optional[str] = None,
    manager_id: Optional[uuid.UUID] = None,
    user_info: Optional[UserInfoProtocol] = None,
) -> dict:
    tenant_id = get_tenant_id()
    qs = Request.objects.filter(tenant_id=tenant_id, is_deleted=False)

    if status:
        qs = qs.filter(status=status)
    if resource_type:
        qs = qs.filter(resource_type=resource_type)
    if manager_id:
        qs = qs.filter(manager_id=manager_id)

    if cursor:
        qs = qs.filter(id__lt=cursor)

    qs = qs.order_by("-id")
    page = list(qs[: limit + 1])
    has_next = len(page) > limit
    if has_next:
        page = page[:limit]

    display_map = _build_display_map(page, tenant_id, user_info)
    return RequestListResponse(
        results=[_to_response(r, display_map) for r in page],
        next_cursor=str(page[-1].id) if has_next and page else None,
        count=len(page),
    )


def get_request_detail(
    request_id: uuid.UUID,
    user_info: Optional[UserInfoProtocol] = None,
):
    from django.core.exceptions import PermissionDenied

    tenant_id = get_tenant_id()
    req = Request.objects.filter(id=request_id, tenant_id=tenant_id, is_deleted=False).first()
    if not req:
        raise PermissionDenied("Заявка не найдена.")
    display_map = _build_display_map([req], tenant_id, user_info)
    return _to_response(req, display_map)
