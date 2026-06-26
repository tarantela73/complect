"""Query-layer tests — pagination correctness and query count bounds."""
from __future__ import annotations

import uuid

import pytest

from apps.tasks.models.task import TaskStatus
from apps.tasks.queries.task_queries import get_task_detail, get_tasks_page
from apps.tasks.schemas.task_dto import TaskListOut, TaskOut
from apps.tasks.services.task_service import TaskService


def _make_task(tenant_id: uuid.UUID, title: str = "T") -> object:
    return TaskService.create(
        tenant_id=tenant_id,
        created_by=uuid.uuid4(),
        title=title,
    )


@pytest.mark.django_db
class TestGetTasksPage:
    def test_returns_task_list_out(self):
        tenant_id = uuid.uuid4()
        _make_task(tenant_id)
        result = get_tasks_page(tenant_id, limit=10)
        assert isinstance(result, TaskListOut)

    def test_empty_tenant_returns_empty_list(self):
        result = get_tasks_page(uuid.uuid4(), limit=10)
        assert result.items == []
        assert result.next_cursor is None

    def test_has_next_cursor_when_more_exist(self):
        tenant_id = uuid.uuid4()
        for i in range(3):
            _make_task(tenant_id, f"Task {i}")
        result = get_tasks_page(tenant_id, limit=2)
        assert result.next_cursor is not None
        assert len(result.items) == 2

    def test_no_next_cursor_when_exactly_limit(self):
        tenant_id = uuid.uuid4()
        for i in range(2):
            _make_task(tenant_id, f"Task {i}")
        result = get_tasks_page(tenant_id, limit=2)
        assert result.next_cursor is None
        assert len(result.items) == 2

    def test_cursor_excludes_already_seen(self):
        tenant_id = uuid.uuid4()
        for i in range(4):
            _make_task(tenant_id, f"Task {i:02d}")
        page1 = get_tasks_page(tenant_id, limit=2)
        page2 = get_tasks_page(tenant_id, limit=2, cursor=page1.next_cursor)
        ids_page1 = {item.id for item in page1.items}
        ids_page2 = {item.id for item in page2.items}
        assert ids_page1.isdisjoint(ids_page2)

    def test_items_are_task_out_instances(self):
        tenant_id = uuid.uuid4()
        _make_task(tenant_id)
        result = get_tasks_page(tenant_id, limit=10)
        assert all(isinstance(item, TaskOut) for item in result.items)

    def test_tenant_isolation(self):
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        _make_task(tenant_a)
        _make_task(tenant_b)
        result = get_tasks_page(tenant_a, limit=10)
        assert len(result.items) == 1

    def test_status_filter(self):
        tenant_id = uuid.uuid4()
        task = _make_task(tenant_id)
        actor = task.created_by_id
        TaskService.change_status(task, TaskStatus.IN_PROGRESS, actor)
        _make_task(tenant_id)

        result = get_tasks_page(tenant_id, limit=10, status=TaskStatus.IN_PROGRESS)
        assert len(result.items) == 1
        assert result.items[0].status == TaskStatus.IN_PROGRESS

    def test_invalid_cursor_returns_empty(self):
        tenant_id = uuid.uuid4()
        _make_task(tenant_id)
        result = get_tasks_page(tenant_id, limit=10, cursor="not-a-valid-cursor")
        assert result.items == []


@pytest.mark.django_db
class TestGetTaskDetail:
    def test_returns_task_out(self):
        tenant_id = uuid.uuid4()
        task = _make_task(tenant_id)
        result = get_task_detail(task.id, tenant_id)
        assert isinstance(result, TaskOut)
        assert result.id == task.id

    def test_returns_none_for_missing(self):
        assert get_task_detail(uuid.uuid4(), uuid.uuid4()) is None

    def test_tenant_isolation(self):
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        task = _make_task(tenant_a)
        assert get_task_detail(task.id, tenant_b) is None
