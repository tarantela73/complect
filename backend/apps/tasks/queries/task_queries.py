"""CQRS Query Side — read-only projections, no writes."""
from __future__ import annotations

import uuid
from typing import Optional

from django.db.models import Q

from apps.tasks.models.task import Task
from apps.tasks.models.task_attachment import TaskAttachment
from apps.tasks.models.task_comment import TaskComment


def _task_qs(tenant_id: uuid.UUID):
    return Task.objects.filter(tenant_id=tenant_id, is_deleted=False).order_by("-created_at", "-id")


def get_tasks_page(
    tenant_id: uuid.UUID,
    *,
    cursor: Optional[uuid.UUID] = None,
    limit: int = 20,
    status: Optional[str] = None,
    assigned_to_id: Optional[uuid.UUID] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[uuid.UUID] = None,
    created_by_id: Optional[uuid.UUID] = None,
) -> dict:
    qs = _task_qs(tenant_id)

    if cursor:
        anchor = Task.objects.filter(id=cursor, tenant_id=tenant_id).values("created_at", "id").first()
        if anchor is None:
            return {"items": [], "next_cursor": None}
        qs = qs.filter(
            Q(created_at__lt=anchor["created_at"])
            | Q(created_at=anchor["created_at"], id__lt=anchor["id"])
        )
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

    return {
        "items": [_task_to_dict(t) for t in rows],
        "next_cursor": str(rows[-1].id) if has_next and rows else None,
    }


def get_task_detail(task_id: uuid.UUID, tenant_id: uuid.UUID) -> Optional[dict]:
    task = _task_qs(tenant_id).filter(id=task_id).first()
    if not task:
        return None
    return _task_to_dict(task)


def get_task_attachments(task_id: uuid.UUID, tenant_id: uuid.UUID) -> list[dict]:
    attachments = TaskAttachment.objects.filter(
        task_id=task_id,
        tenant_id=tenant_id,
        is_deleted=False,
    ).order_by("-id")
    return [
        {
            "id": a.id,
            "file_id": a.file_id,
            "attached_by": a.attached_by_id,
            "created_at": a.created_at,
        }
        for a in attachments
    ]


def get_task_comments(task_id: uuid.UUID, tenant_id: uuid.UUID) -> list[dict]:
    comments = TaskComment.objects.filter(
        task_id=task_id,
        tenant_id=tenant_id,
        is_deleted=False,
    ).order_by("created_at")
    return [
        {
            "id": c.id,
            "body": c.body,
            "author_id": c.author_id,
            "created_at": c.created_at,
        }
        for c in comments
    ]


def _task_to_dict(t: Task) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "status": t.status,
        "priority": t.priority,
        "created_by": t.created_by_id,
        "assigned_to_id": t.assigned_to_id,
        "due_date": t.due_date,
        "entity_type": t.entity_type,
        "entity_id": t.entity_id,
        "closed_at": t.closed_at,
        "closed_by_id": t.closed_by_id,
        "tenant_id": t.tenant_id,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "is_legal_hold": t.is_legal_hold,
        "version": t.version,
    }
