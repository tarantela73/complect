from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class TaskStatus(models.TextChoices):
    OPEN = "open", "Открыта"
    IN_PROGRESS = "in_progress", "В работе"
    DONE = "done", "Выполнена"
    CANCELLED = "cancelled", "Отменена"


class TaskPriority(models.TextChoices):
    LOW = "low", "Низкий"
    NORMAL = "normal", "Нормальный"
    HIGH = "high", "Высокий"


class Task(BaseEnterpriseModel):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=TaskStatus.choices,
        default=TaskStatus.OPEN,
        db_index=True,
    )
    priority = models.CharField(
        max_length=10,
        choices=TaskPriority.choices,
        default=TaskPriority.NORMAL,
    )
    created_by_id = models.UUIDField(db_index=True)
    assigned_to_id = models.UUIDField(null=True, blank=True, db_index=True)
    due_date = models.DateField(null=True, blank=True)
    # Cross-domain link: одно из contract|object|contragent|request|''
    entity_type = models.CharField(max_length=50, blank=True, default="")
    entity_id = models.UUIDField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "tasks_tasks"
        indexes = [
            models.Index(fields=["tenant_id", "status", "is_deleted"]),
            models.Index(fields=["tenant_id", "assigned_to_id", "is_deleted"]),
        ]
