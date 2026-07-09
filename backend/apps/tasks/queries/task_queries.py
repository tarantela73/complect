"""CQRS Query Side — read-only projections, no writes."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from typing import Optional

from django.db.models import Q

from apps.tasks.models.task import Task
from apps.tasks.models.task_attachment import TaskAttachment
from apps.tasks.models.task_comment import TaskComment
from apps.tasks.schemas.task_dto import TaskAttachmentOut, TaskCommentOut, TaskListOut, TaskOut


def _encode_cursor(created_at: datetime, task_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}:{task_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts_str, id_str = raw.rsplit(":", 1)
        return datetime.fromisoformat(ts_str), uuid.UUID(id_str)
    except Exception:
        return None


def _task_qs(tenant_id: uuid.UUID):
    return Task.objects.filter(tenant_id=tenant_id, is_deleted=False).order_by("-created_at", "-id")


def get_tasks_page(
    tenant_id: uuid.UUID,
    *,
    cursor: Optional[str] = None,
    limit: int = 20,
    status: Optional[str] = None,
    assigned_to_id: Optional[uuid.UUID] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[uuid.UUID] = None,
    created_by_id: Optional[uuid.UUID] = None,
) -> TaskListOut:
    qs = _task_qs(tenant_id)

    if cursor:
        anchor = _decode_cursor(cursor)
        if anchor is None:
            return TaskListOut(items=[], next_cursor=None)
        anchor_ts, anchor_id = anchor
        qs = qs.filter(Q(created_at__lt=anchor_ts) | Q(created_at=anchor_ts, id__lt=anchor_id))
    if status:
        qs = qs.filter(status=status)
    if assigned_to_id:
        qs = qs.filter(assigned_to_id=assigned_to_id)
    if entity_type:
        qs = qs.filter(entity_type=entity_type)
    if entity_id:
        qs = qs.filter(entity_id=entity_id)
    if created_by_id:
        qs = qs.filter(created_by_id=created_by_id)

    rows = list(qs[: limit + 1])
    has_next = len(rows) > limit
    rows = rows[:limit]

    next_cursor = _encode_cursor(rows[-1].created_at, rows[-1].id) if has_next and rows else None

    return TaskListOut(items=[_task_to_out(t) for t in rows], next_cursor=next_cursor)


def get_task_detail(task_id: uuid.UUID, tenant_id: uuid.UUID) -> Optional[TaskOut]:
    task = _task_qs(tenant_id).filter(id=task_id).first()
    if not task:
        return None
    return _task_to_out(task)


def get_task_attachments(task_id: uuid.UUID, tenant_id: uuid.UUID) -> list[TaskAttachmentOut]:
    attachments = TaskAttachment.objects.filter(
        task_id=task_id,
        tenant_id=tenant_id,
        is_deleted=False,
    ).order_by("-id")
    return [
        TaskAttachmentOut(
            id=a.id,
            file_id=a.file_id,
            attached_by=a.attached_by_id,
            created_at=a.created_at,
        )
        for a in attachments
    ]


def get_task_comments(task_id: uuid.UUID, tenant_id: uuid.UUID) -> list[TaskCommentOut]:
    comments = TaskComment.objects.filter(
        task_id=task_id,
        tenant_id=tenant_id,
        is_deleted=False,
    ).order_by("created_at")
    return [
        TaskCommentOut(
            id=c.id,
            body=c.body,
            author_id=c.author_id,
            created_at=c.created_at,
        )
        for c in comments
    ]


def _task_to_out(t: Task) -> TaskOut:
    return TaskOut(
        id=t.id,
        title=t.title,
        description=t.description,
        status=t.status,
        priority=t.priority,
        created_by=t.created_by_id,
        assigned_to_id=t.assigned_to_id,
        due_date=t.due_date,
        entity_type=t.entity_type,
        entity_id=t.entity_id,
        closed_at=t.closed_at,
        closed_by_id=t.closed_by_id,
        created_at=t.created_at,
        updated_at=t.updated_at,
        is_legal_hold=t.is_legal_hold,
        version=t.version,
    )
