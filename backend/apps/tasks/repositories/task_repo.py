"""CQRS Command Side — TaskRepository: tenant-isolated reads for writes only."""
from __future__ import annotations

import uuid

import django.utils.timezone as tz
from django.db.models import F

from apps.common.repository.sre_engine import ConcurrentModificationError
from apps.tasks.models.task import Task
from apps.tasks.models.task_attachment import TaskAttachment
from apps.tasks.models.task_comment import TaskComment


class TaskRepository:
    @staticmethod
    def get_queryset(tenant_id: uuid.UUID):
        return Task.objects.filter(tenant_id=tenant_id, is_deleted=False)

    @classmethod
    def get_by_id(cls, task_id: uuid.UUID, tenant_id: uuid.UUID) -> Task | None:
        return cls.get_queryset(tenant_id).filter(id=task_id).first()

    @staticmethod
    def save(task: Task) -> Task:
        """Atomic CAS update via version increment. Raises ConcurrentModificationError on conflict."""
        current_version = task.version

        if task._state.adding:
            task.save()
            return task

        updated = (
            Task.objects.filter(
                pk=task.pk,
                tenant_id=task.tenant_id,
                version=current_version,
                is_deleted=False,
            ).update(
                title=task.title,
                description=task.description,
                status=task.status,
                priority=task.priority,
                assigned_to_id=task.assigned_to_id,
                due_date=task.due_date,
                closed_at=task.closed_at,
                closed_by_id=task.closed_by_id,
                version=F("version") + 1,
                updated_at=tz.now(),
            )
        )

        if updated == 0:
            raise ConcurrentModificationError(
                "OCC Conflict: задача была изменена параллельным процессом."
            )

        task.refresh_from_db()
        return task

    @staticmethod
    def create_attachment(attachment: TaskAttachment) -> TaskAttachment:
        attachment.save()
        return attachment

    @staticmethod
    def create_comment(comment: TaskComment) -> TaskComment:
        comment.save()
        return comment
