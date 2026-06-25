"""CQRS Command Side — TaskService: state mutations, no heavy SELECTs."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import django.utils.timezone as tz
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from apps.common.events.tasks import TaskCancelledEvent, TaskCompletedEvent
from apps.common.repository.sre_engine import EnterpriseSreRepository
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.utils.user_utils import user_exists_in_tenant
from apps.tasks.models.task import Task, TaskPriority, TaskStatus
from apps.tasks.models.task_attachment import TaskAttachment
from apps.tasks.models.task_comment import TaskComment
from apps.tasks.repositories.task_repo import TaskRepository

# State machine: allowed transitions
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    TaskStatus.OPEN: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.DONE, TaskStatus.CANCELLED},
    TaskStatus.DONE: set(),
    TaskStatus.CANCELLED: set(),
}

_TERMINAL_STATUSES = {TaskStatus.DONE, TaskStatus.CANCELLED}

_UNSET = object()


class TaskService:
    @staticmethod
    def create(
        tenant_id: uuid.UUID,
        created_by: uuid.UUID,
        title: str,
        description: str = "",
        priority: str = TaskPriority.NORMAL,
        assigned_to_id: uuid.UUID | None = None,
        due_date: date | None = None,
        entity_type: str = "",
        entity_id: uuid.UUID | None = None,
    ) -> Task:
        if priority not in TaskPriority.values:
            raise ValidationError(f"Неверный приоритет: {priority}. Допустимые: {TaskPriority.values}")

        if assigned_to_id is not None and not user_exists_in_tenant(assigned_to_id, tenant_id):
            raise ValidationError(f"Пользователь {assigned_to_id} не найден в данном тенанте.")

        task = Task(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            created_by_id=created_by,
            title=title,
            description=description,
            priority=priority,
            status=TaskStatus.OPEN,
            assigned_to_id=assigned_to_id,
            due_date=due_date,
            entity_type=entity_type or "",
            entity_id=entity_id,
        )
        with transaction.atomic():
            task = TaskRepository.save(task)
        return task

    @staticmethod
    def update(
        task: Task,
        *,
        actor_id: uuid.UUID,
        title: str | None = None,
        description: str | None = None,
        priority: str | None = None,
        assigned_to_id: uuid.UUID | None = _UNSET,  # type: ignore[assignment]
        due_date: date | None = _UNSET,  # type: ignore[assignment]
    ) -> Task:
        if task.status in _TERMINAL_STATUSES:
            raise ValidationError("Закрытую задачу нельзя редактировать.")

        if task.is_legal_hold:
            raise PermissionDenied("LEGAL_HOLD")

        if task.created_by_id != actor_id and task.assigned_to_id != actor_id:
            raise PermissionDenied("Только создатель или исполнитель могут редактировать задачу.")

        if priority is not None and priority not in TaskPriority.values:
            raise ValidationError(f"Неверный приоритет: {priority}. Допустимые: {TaskPriority.values}")

        if assigned_to_id is not _UNSET and assigned_to_id is not None and not user_exists_in_tenant(assigned_to_id, task.tenant_id):
            raise ValidationError(f"Пользователь {assigned_to_id} не найден в данном тенанте.")

        if title is not None:
            task.title = title
        if description is not None:
            task.description = description
        if priority is not None:
            task.priority = priority
        if assigned_to_id is not _UNSET:
            task.assigned_to_id = assigned_to_id
        if due_date is not _UNSET:
            task.due_date = due_date

        with transaction.atomic():
            return TaskRepository.save(task)

    @staticmethod
    def change_status(task: Task, new_status: str, changed_by: uuid.UUID) -> Task:
        if task.is_legal_hold:
            raise PermissionDenied("LEGAL_HOLD")

        if new_status not in TaskStatus.values:
            raise ValidationError(f"Неизвестный статус: {new_status}.")

        allowed = _ALLOWED_TRANSITIONS.get(task.status, set())
        if new_status not in allowed:
            raise ValueError(f"Переход {task.status} → {new_status} недопустим.")

        if changed_by != task.created_by_id and changed_by != task.assigned_to_id:
            raise PermissionDenied("Только создатель или исполнитель могут менять статус задачи.")

        with transaction.atomic():
            task.status = new_status
            if new_status in _TERMINAL_STATUSES:
                task.closed_at = datetime.now(UTC)
                task.closed_by_id = changed_by
            saved = TaskRepository.save(task)
            correlation_id = uuid.uuid4()
            if new_status == TaskStatus.DONE:
                TransactionalOutboxService.push_to_outbox(
                    TaskCompletedEvent.create(
                        correlation_id=correlation_id,
                        task_id=saved.id,
                        tenant_id=saved.tenant_id,
                        task_title=saved.title,
                        creator_id=saved.created_by_id,
                        assigned_to_id=saved.assigned_to_id,
                    )
                )
            elif new_status == TaskStatus.CANCELLED:
                TransactionalOutboxService.push_to_outbox(
                    TaskCancelledEvent.create(
                        correlation_id=correlation_id,
                        task_id=saved.id,
                        tenant_id=saved.tenant_id,
                        task_title=saved.title,
                        creator_id=saved.created_by_id,
                        assigned_to_id=saved.assigned_to_id,
                    )
                )
            return saved

    @staticmethod
    def cancel(task: Task, cancelled_by: uuid.UUID) -> Task:
        return TaskService.change_status(task, TaskStatus.CANCELLED, cancelled_by)

    @staticmethod
    def attach_file(task: Task, file_id: uuid.UUID, attached_by: uuid.UUID) -> TaskAttachment:
        if task.is_legal_hold:
            raise PermissionDenied("LEGAL_HOLD")
        try:
            with transaction.atomic():
                existing = TaskAttachment.objects.select_for_update().filter(
                    task_id=task.id,
                    file_id=file_id,
                    tenant_id=task.tenant_id,
                    is_deleted=False,
                ).first()
                if existing is not None:
                    raise ValidationError("Этот файл уже прикреплён к задаче.")

                attachment = TaskAttachment(
                    id=uuid.uuid4(),
                    tenant_id=task.tenant_id,
                    task_id=task.id,
                    file_id=file_id,
                    attached_by_id=attached_by,
                )
                return TaskRepository.create_attachment(attachment)
        except IntegrityError:
            raise ValidationError("Этот файл уже прикреплён к задаче.") from None

    @staticmethod
    def detach_file(task: Task, file_id: uuid.UUID, actor_id: uuid.UUID) -> None:
        if task.is_legal_hold:
            raise PermissionDenied("LEGAL_HOLD")

        attachment = TaskAttachment.objects.filter(
            task_id=task.id,
            file_id=file_id,
            tenant_id=task.tenant_id,
            is_deleted=False,
        ).first()
        if attachment is None:
            raise PermissionDenied("ATTACHMENT_NOT_FOUND")

        if attachment.attached_by_id != actor_id:
            raise PermissionDenied("NOT_ATTACHMENT_OWNER")

        with transaction.atomic():
            EnterpriseSreRepository.atomic_cas_update(
                attachment,
                is_deleted=True,
                deleted_at=tz.now(),
            )

    @staticmethod
    def add_comment(task: Task, author_id: uuid.UUID, body: str) -> "TaskComment":
        if task.is_legal_hold:
            raise PermissionDenied("LEGAL_HOLD")
        if task.status in _TERMINAL_STATUSES:
            raise ValidationError("Нельзя комментировать закрытую задачу.")
        if not body.strip():
            raise ValidationError("Комментарий не может быть пустым.")

        comment = TaskComment(
            id=uuid.uuid4(),
            tenant_id=task.tenant_id,
            task_id=task.id,
            author_id=author_id,
            body=body.strip(),
        )
        with transaction.atomic():
            comment = TaskRepository.create_comment(comment)
        return comment
