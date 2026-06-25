"""Unit tests — TaskService state machine and business rules."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

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
