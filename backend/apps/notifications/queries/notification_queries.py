from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class NotificationDTO:
    id: uuid.UUID
    title: str
    body: str
    event_type: str
    is_read: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NotificationPage:
    results: list[NotificationDTO]
    next_cursor: uuid.UUID | None
    count: int


def get_notifications_page(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    cursor: uuid.UUID | None = None,
    limit: int = 20,
) -> NotificationPage:
    from apps.notifications.models import InAppNotification

    qs = InAppNotification.objects.filter(
        tenant_id=tenant_id,
        user_id=user_id,
        is_deleted=False,
        archived_at__isnull=True,
    )

    if cursor is not None:
        try:
            anchor = InAppNotification.objects.get(id=cursor, tenant_id=tenant_id)
            qs = qs.filter(created_at__lt=anchor.created_at)
        except InAppNotification.DoesNotExist:
            pass

    total = qs.count()
    records = list(qs.order_by("-created_at")[: limit + 1])
    has_next = len(records) > limit
    page_records = records[:limit]

    results = [
        NotificationDTO(
            id=r.id,
            title=r.title,
            body=r.body,
            event_type=r.event_type,
            is_read=r.read_at is not None,
            created_at=r.created_at,
        )
        for r in page_records
    ]

    return NotificationPage(
        results=results,
        next_cursor=page_records[-1].id if has_next else None,
        count=total,
    )


def get_unread_count(*, tenant_id: uuid.UUID, user_id: uuid.UUID) -> int:
    from apps.notifications.models import InAppNotification

    return InAppNotification.objects.filter(
        tenant_id=tenant_id,
        user_id=user_id,
        is_deleted=False,
        archived_at__isnull=True,
        read_at__isnull=True,
    ).count()
