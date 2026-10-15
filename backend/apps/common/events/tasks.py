import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import ClassVar, Optional

_STATUS_LABELS = {
    "open": "Открыта",
    "in_progress": "В работе",
    "done": "Выполнена",
    "cancelled": "Отменена",
}


@dataclass(slots=True, frozen=True)
class TaskStatusChangedEvent:
    EVENT_TYPE: ClassVar[str] = "tasks.TaskStatusChanged"
    event_id: uuid.UUID
    correlation_id: uuid.UUID
    occurred_at: datetime
    task_id: uuid.UUID
    tenant_id: uuid.UUID
    task_title: str
    old_status: str
    new_status: str
    changed_by_id: uuid.UUID
    creator_id: uuid.UUID
    assigned_to_id: Optional[uuid.UUID]
    event_version: int = 1

    @classmethod
    def create(
        cls,
        correlation_id: uuid.UUID,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        task_title: str,
        old_status: str,
        new_status: str,
        changed_by_id: uuid.UUID,
        creator_id: uuid.UUID,
        assigned_to_id: Optional[uuid.UUID],
    ) -> "TaskStatusChangedEvent":
        return cls(
            event_id=uuid.uuid4(),
            correlation_id=correlation_id,
            occurred_at=datetime.now(tz=timezone.utc),
            task_id=task_id,
            tenant_id=tenant_id,
            task_title=task_title,
            old_status=old_status,
            new_status=new_status,
            changed_by_id=changed_by_id,
            creator_id=creator_id,
            assigned_to_id=assigned_to_id,
        )


@dataclass(slots=True, frozen=True)
class TaskAssignedEvent:
    EVENT_TYPE: ClassVar[str] = "tasks.TaskAssigned"
    event_id: uuid.UUID
    correlation_id: uuid.UUID
    occurred_at: datetime
    task_id: uuid.UUID
    tenant_id: uuid.UUID
    task_title: str
    assigned_to_id: uuid.UUID
    assigned_by_id: uuid.UUID
    event_version: int = 1

    @classmethod
    def create(
        cls,
        correlation_id: uuid.UUID,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        task_title: str,
        assigned_to_id: uuid.UUID,
        assigned_by_id: uuid.UUID,
    ) -> "TaskAssignedEvent":
        return cls(
            event_id=uuid.uuid4(),
            correlation_id=correlation_id,
            occurred_at=datetime.now(tz=timezone.utc),
            task_id=task_id,
            tenant_id=tenant_id,
            task_title=task_title,
            assigned_to_id=assigned_to_id,
            assigned_by_id=assigned_by_id,
        )


@dataclass(slots=True, frozen=True)
class TaskCompletedEvent:
    EVENT_TYPE: ClassVar[str] = "tasks.TaskCompleted"
    event_id: uuid.UUID
    correlation_id: uuid.UUID
    occurred_at: datetime
    task_id: uuid.UUID
    tenant_id: uuid.UUID
    task_title: str
    creator_id: uuid.UUID
    assigned_to_id: Optional[uuid.UUID]
    event_version: int = 1

    @classmethod
    def create(
        cls,
        correlation_id: uuid.UUID,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        task_title: str,
        creator_id: uuid.UUID,
        assigned_to_id: Optional[uuid.UUID],
    ) -> "TaskCompletedEvent":
        return cls(
            event_id=uuid.uuid4(),
            correlation_id=correlation_id,
            occurred_at=datetime.now(tz=timezone.utc),
            task_id=task_id,
            tenant_id=tenant_id,
            task_title=task_title,
            creator_id=creator_id,
            assigned_to_id=assigned_to_id,
        )


@dataclass(slots=True, frozen=True)
class TaskCancelledEvent:
    EVENT_TYPE: ClassVar[str] = "tasks.TaskCancelled"
    event_id: uuid.UUID
    correlation_id: uuid.UUID
    occurred_at: datetime
    task_id: uuid.UUID
    tenant_id: uuid.UUID
    task_title: str
    creator_id: uuid.UUID
    assigned_to_id: Optional[uuid.UUID]
    event_version: int = 1

    @classmethod
    def create(
        cls,
        correlation_id: uuid.UUID,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        task_title: str,
        creator_id: uuid.UUID,
        assigned_to_id: Optional[uuid.UUID],
    ) -> "TaskCancelledEvent":
        return cls(
            event_id=uuid.uuid4(),
            correlation_id=correlation_id,
            occurred_at=datetime.now(tz=timezone.utc),
            task_id=task_id,
            tenant_id=tenant_id,
            task_title=task_title,
            creator_id=creator_id,
            assigned_to_id=assigned_to_id,
        )
