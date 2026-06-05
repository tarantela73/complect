import uuid
from datetime import datetime
from typing import Optional

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.utils import timezone
from ninja import Query, Router, Schema

from apps.common.api.errors import make_error_response
from apps.common.api.permissions import require_permission
from apps.common.tenancy.context_core import get_context
from apps.notifications.models import InAppNotification
from apps.notifications.queries.notification_queries import (
    get_notifications_page,
    get_unread_count,
)

router = Router(tags=["notifications"])


class NotificationSchema(Schema):
    id: uuid.UUID
    title: str
    body: str
    event_type: str
    is_read: bool
    created_at: datetime


class NotificationListResponse(Schema):
    results: list[NotificationSchema]
    next_cursor: Optional[uuid.UUID] = None
    count: int


class UnreadCountResponse(Schema):
    count: int


@router.get("/", response={200: NotificationListResponse, 403: dict})
@require_permission("read", "notifications")
def list_notifications(
    request: HttpRequest,
    cursor: Optional[uuid.UUID] = Query(None),  # noqa: B008
    limit: int = Query(20, ge=1, le=100),  # noqa: B008
):
    ctx = get_context()
    page = get_notifications_page(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        cursor=cursor,
        limit=limit,
    )
    return 200, NotificationListResponse(
        results=[
            NotificationSchema(
                id=n.id,
                title=n.title,
                body=n.body,
                event_type=n.event_type,
                is_read=n.is_read,
                created_at=n.created_at,
            )
            for n in page.results
        ],
        next_cursor=page.next_cursor,
        count=page.count,
    )


@router.get("/unread-count/", response={200: UnreadCountResponse, 403: dict})
@require_permission("read", "notifications")
def unread_count(request: HttpRequest):
    ctx = get_context()
    count = get_unread_count(tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    return 200, UnreadCountResponse(count=count)


@router.post("/{notif_id}/read/", response={200: dict, 403: dict, 404: dict})
@require_permission("read", "notifications")
def mark_read(request: HttpRequest, notif_id: uuid.UUID):
    ctx = get_context()
    updated = InAppNotification.objects.filter(
        id=notif_id,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        read_at__isnull=True,
    ).update(read_at=timezone.now())
    if not updated:
        exists = InAppNotification.objects.filter(
            id=notif_id, tenant_id=ctx.tenant_id, user_id=ctx.user_id
        ).exists()
        if not exists:
            return 404, make_error_response("NOTIFICATION_NOT_FOUND", "Уведомление не найдено.")
    return 200, {"ok": True}


@router.post("/read-all/", response={200: dict, 403: dict})
@require_permission("read", "notifications")
def mark_all_read(request: HttpRequest):
    ctx = get_context()
    InAppNotification.objects.filter(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        read_at__isnull=True,
        is_deleted=False,
    ).update(read_at=timezone.now())
    return 200, {"ok": True}
