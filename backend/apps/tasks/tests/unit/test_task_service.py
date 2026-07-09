"""Unit tests — TaskService state machine and business rules."""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.common.models.outbox import OutboxRecord
from apps.tasks.models.task import TaskStatus
from apps.tasks.services.task_service import TaskService


def _create(tenant_id: uuid.UUID | None = None, **kwargs) -> object:
    return TaskService.create(
        tenant_id=tenant_id or uuid.uuid4(),
        created_by=uuid.uuid4(),
        title=kwargs.pop("title", "Default title"),
        **kwargs,
    )


@pytest.mark.django_db
class TestTaskServiceCreate:
    def test_creates_with_open_status(self):
        task = _create()
        assert task.status == TaskStatus.OPEN

    def test_invalid_priority_raises(self):
        with pytest.raises(ValidationError):
            _create(priority="invalid")

    def test_title_stored(self):
        task = _create(title="My Task")
        assert task.title == "My Task"


@pytest.mark.django_db
class TestTaskServiceStateMachine:
    def test_open_to_in_progress_allowed(self):
        task = _create()
        actor = task.created_by_id
        updated = TaskService.change_status(task, TaskStatus.IN_PROGRESS, actor)
        assert updated.status == TaskStatus.IN_PROGRESS

    def test_open_to_done_forbidden(self):
        task = _create()
        actor = task.created_by_id
        with pytest.raises(ValueError, match="недопустим"):
            TaskService.change_status(task, TaskStatus.DONE, actor)

    def test_done_is_terminal(self):
        task = _create()
        actor = task.created_by_id
        task = TaskService.change_status(task, TaskStatus.IN_PROGRESS, actor)
        task = TaskService.change_status(task, TaskStatus.DONE, actor)
        with pytest.raises(ValueError, match="недопустим"):
            TaskService.change_status(task, TaskStatus.OPEN, actor)

    def test_cancel_sets_closed_at(self):
        task = _create()
        actor = task.created_by_id
        cancelled = TaskService.cancel(task, actor)
        assert cancelled.closed_at is not None
        assert cancelled.closed_by_id == actor

    def test_non_participant_cannot_change_status(self):
        task = _create()
        stranger = uuid.uuid4()
        with pytest.raises(PermissionDenied):
            TaskService.change_status(task, TaskStatus.IN_PROGRESS, stranger)


@pytest.mark.django_db
class TestTaskServiceUpdate:
    def test_update_title(self):
        task = _create()
        actor = task.created_by_id
        updated = TaskService.update(task, actor_id=actor, title="New Title")
        assert updated.title == "New Title"

    def test_update_closed_task_raises(self):
        task = _create()
        actor = task.created_by_id
        task = TaskService.cancel(task, actor)
        with pytest.raises(ValidationError, match="Закрытую"):
            TaskService.update(task, actor_id=actor, title="New Title")

    def test_due_date_cleared_when_none_passed(self):
        task = _create(due_date=date(2030, 1, 1))
        actor = task.created_by_id
        updated = TaskService.update(task, actor_id=actor, due_date=None)
        assert updated.due_date is None

    def test_assigned_to_preserved_when_not_in_kwargs(self):
        assignee = uuid.uuid4()
        task = _create()
        actor = task.created_by_id
        task.assigned_to_id = assignee
        task.save()
        from apps.tasks.services.task_service import _UNSET

        updated = TaskService.update(task, actor_id=actor, assigned_to_id=_UNSET)
        assert updated.assigned_to_id == assignee

    def test_legal_hold_blocks_update(self):
        task = _create()
        task.is_legal_hold = True
        task.save()
        with pytest.raises(PermissionDenied, match="LEGAL_HOLD"):
            TaskService.update(task, actor_id=task.created_by_id, title="X")


@pytest.mark.django_db
class TestTaskServiceComment:
    def test_add_comment_on_open_task(self):
        task = _create()
        comment = TaskService.add_comment(task, task.created_by_id, "Hello")
        assert comment.body == "Hello"

    def test_blank_comment_raises(self):
        task = _create()
        with pytest.raises(ValidationError, match="пустым"):
            TaskService.add_comment(task, task.created_by_id, "   ")

    def test_comment_on_cancelled_task_raises(self):
        task = _create()
        actor = task.created_by_id
        task = TaskService.cancel(task, actor)
        with pytest.raises(ValidationError, match="закрытую"):
            TaskService.add_comment(task, actor, "Late comment")


@pytest.mark.django_db
class TestTaskServiceOutbox:
    def test_done_transition_emits_completed_event(self):
        from apps.common.events.tasks import TaskCompletedEvent

        task = _create()
        actor = task.created_by_id
        task = TaskService.change_status(task, TaskStatus.IN_PROGRESS, actor)
        TaskService.change_status(task, TaskStatus.DONE, actor)

        assert OutboxRecord.objects.filter(event_type=TaskCompletedEvent.EVENT_TYPE).exists()

    def test_cancel_emits_cancelled_event(self):
        from apps.common.events.tasks import TaskCancelledEvent

        task = _create()
        actor = task.created_by_id
        TaskService.cancel(task, actor)

        assert OutboxRecord.objects.filter(event_type=TaskCancelledEvent.EVENT_TYPE).exists()

    def test_done_event_payload_contains_tenant_id(self):
        from apps.common.events.tasks import TaskCompletedEvent
        from apps.common.tenancy.context import set_tenant_context

        tenant_id = uuid.uuid4()
        set_tenant_context(tenant_id)
        task = _create(tenant_id=tenant_id)
        actor = task.created_by_id
        task = TaskService.change_status(task, TaskStatus.IN_PROGRESS, actor)
        TaskService.change_status(task, TaskStatus.DONE, actor)

        record = OutboxRecord.objects.filter(event_type=TaskCompletedEvent.EVENT_TYPE).first()
        assert record is not None
        assert str(tenant_id) == record.payload_json.get("tenant_id")

    def test_status_changed_event_emitted_on_every_transition(self):
        from apps.common.events.tasks import TaskStatusChangedEvent

        task = _create()
        actor = task.created_by_id
        TaskService.change_status(task, TaskStatus.IN_PROGRESS, actor)

        record = OutboxRecord.objects.filter(event_type=TaskStatusChangedEvent.EVENT_TYPE).first()
        assert record is not None
        assert record.payload_json.get("old_status") == "open"
        assert record.payload_json.get("new_status") == "in_progress"
        assert record.payload_json.get("changed_by_id") == str(actor)

    def test_status_changed_event_on_done(self):
        from apps.common.events.tasks import TaskStatusChangedEvent

        task = _create()
        actor = task.created_by_id
        TaskService.change_status(task, TaskStatus.IN_PROGRESS, actor)
        TaskService.change_status(task, TaskStatus.DONE, actor)

        records = OutboxRecord.objects.filter(event_type=TaskStatusChangedEvent.EVENT_TYPE)
        assert records.count() == 2
        last = records.order_by("-created_at").first()
        assert last.payload_json.get("old_status") == "in_progress"
        assert last.payload_json.get("new_status") == "done"

    def test_status_changed_event_on_cancel(self):
        from apps.common.events.tasks import TaskStatusChangedEvent

        task = _create()
        actor = task.created_by_id
        TaskService.cancel(task, actor)

        record = OutboxRecord.objects.filter(event_type=TaskStatusChangedEvent.EVENT_TYPE).first()
        assert record is not None
        assert record.payload_json.get("old_status") == "open"
        assert record.payload_json.get("new_status") == "cancelled"

    def test_no_outbox_event_on_intermediate_transition(self):
        from apps.common.events.tasks import TaskCancelledEvent, TaskCompletedEvent

        task = _create()
        actor = task.created_by_id
        TaskService.change_status(task, TaskStatus.IN_PROGRESS, actor)

        assert not OutboxRecord.objects.filter(
            event_type__in=[TaskCompletedEvent.EVENT_TYPE, TaskCancelledEvent.EVENT_TYPE]
        ).exists()


@pytest.mark.django_db
class TestTaskServiceAssigneeNotification:
    @patch("apps.tasks.services.task_service.user_exists_in_tenant", return_value=True)
    def test_create_with_assignee_emits_assigned_event(self, _mock):
        from apps.common.events.tasks import TaskAssignedEvent

        assignee = uuid.uuid4()
        creator = uuid.uuid4()
        tenant = uuid.uuid4()
        TaskService.create(
            tenant_id=tenant,
            created_by=creator,
            title="Assigned task",
            assigned_to_id=assignee,
        )

        record = OutboxRecord.objects.filter(event_type=TaskAssignedEvent.EVENT_TYPE).first()
        assert record is not None
        assert record.payload_json.get("assigned_to_id") == str(assignee)
        assert record.payload_json.get("assigned_by_id") == str(creator)
        assert record.payload_json.get("task_title") == "Assigned task"

    def test_create_without_assignee_no_assigned_event(self):
        from apps.common.events.tasks import TaskAssignedEvent

        TaskService.create(
            tenant_id=uuid.uuid4(),
            created_by=uuid.uuid4(),
            title="No assignee",
        )

        assert not OutboxRecord.objects.filter(event_type=TaskAssignedEvent.EVENT_TYPE).exists()

    @patch("apps.tasks.services.task_service.user_exists_in_tenant", return_value=True)
    def test_update_assignee_emits_assigned_event(self, _mock):
        from apps.common.events.tasks import TaskAssignedEvent

        task = _create()
        actor = task.created_by_id
        new_assignee = uuid.uuid4()

        TaskService.update(task, actor_id=actor, assigned_to_id=new_assignee)

        record = OutboxRecord.objects.filter(event_type=TaskAssignedEvent.EVENT_TYPE).first()
        assert record is not None
        assert record.payload_json.get("assigned_to_id") == str(new_assignee)

    @patch("apps.tasks.services.task_service.user_exists_in_tenant", return_value=True)
    def test_update_same_assignee_no_new_event(self, _mock):
        from apps.common.events.tasks import TaskAssignedEvent

        assignee = uuid.uuid4()
        task = _create()
        actor = task.created_by_id
        task.assigned_to_id = assignee
        task.save()

        TaskService.update(task, actor_id=actor, assigned_to_id=assignee)

        assert not OutboxRecord.objects.filter(event_type=TaskAssignedEvent.EVENT_TYPE).exists()

    def test_unassign_does_not_emit_event(self):
        from apps.common.events.tasks import TaskAssignedEvent

        assignee = uuid.uuid4()
        task = _create()
        actor = task.created_by_id
        task.assigned_to_id = assignee
        task.save()

        TaskService.update(task, actor_id=actor, assigned_to_id=None)

        assert not OutboxRecord.objects.filter(event_type=TaskAssignedEvent.EVENT_TYPE).exists()

    @patch("apps.tasks.services.task_service.user_exists_in_tenant", return_value=True)
    def test_reassign_emits_event_with_new_assignee(self, _mock):
        from apps.common.events.tasks import TaskAssignedEvent

        old_assignee = uuid.uuid4()
        new_assignee = uuid.uuid4()
        task = _create()
        actor = task.created_by_id
        task.assigned_to_id = old_assignee
        task.save()

        TaskService.update(task, actor_id=actor, assigned_to_id=new_assignee)

        record = OutboxRecord.objects.filter(event_type=TaskAssignedEvent.EVENT_TYPE).first()
        assert record is not None
        assert record.payload_json.get("assigned_to_id") == str(new_assignee)
        assert record.payload_json.get("assigned_by_id") == str(actor)
