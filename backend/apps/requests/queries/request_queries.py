import uuid
from typing import Optional

from apps.common.tenancy.context_core import get_tenant_id
from apps.requests.models.request import Request
from apps.requests.schemas.request_dto import RequestListResponse, RequestResponse


def _to_response(req: Request) -> RequestResponse:
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
        converted_object_id=req.converted_object_id,
        converted_at=req.converted_at,
        created_at=req.created_at,
    )


def get_requests_page(
    cursor: Optional[uuid.UUID] = None,
    limit: int = 20,
    status: Optional[str] = None,
    resource_type: Optional[str] = None,
    manager_id: Optional[uuid.UUID] = None,
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

    return RequestListResponse(
        results=[_to_response(r) for r in page],
        next_cursor=str(page[-1].id) if has_next and page else None,
        count=len(page),
    )


def get_request_detail(request_id: uuid.UUID):
    from django.core.exceptions import PermissionDenied

    tenant_id = get_tenant_id()
    req = Request.objects.filter(
        id=request_id, tenant_id=tenant_id, is_deleted=False
    ).first()
    if not req:
        raise PermissionDenied("Заявка не найдена.")
    return _to_response(req)
