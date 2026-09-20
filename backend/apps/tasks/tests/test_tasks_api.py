"""
Tasks API tests (U4 — Sprint C).

Coverage:
  - GET /api/v1/tasks/ — list with permission/without permission
  - POST /api/v1/tasks/ — create task
  - GET /api/v1/tasks/{id}/ — detail
  - PATCH /api/v1/tasks/{id}/ — update
  - POST /api/v1/tasks/{id}/status/ — state machine transitions
  - POST /api/v1/tasks/{id}/cancel/ — cancel
  - POST /api/v1/tasks/{id}/files/ — attach file
  - DELETE /api/v1/tasks/{id}/files/{file_id}/ — detach file
  - Tenant isolation: tasks of other tenants not visible
  - Cursor pagination
"""

import uuid
from dataclasses import dataclass

import pytest
from django.test import Client

from apps.users.models import User
from apps.users.services.auth_service import _make_access_token, _pk_to_uuid4

GW_TOKEN = "complect_internal_secure_mesh_token_2026"
BASE_URL = "/api/v1/tasks/"


@dataclass(frozen=True)
class _Actor:
    tenant_id: uuid.UUID
    actor_id: uuid.UUID
    token: str


def _make_actor(tenant_id: uuid.UUID) -> _Actor:
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(
        username=f"task_user_{suffix}",
        email=f"task_user_{suffix}@example.com",
        password="Test123!",
        tenant_id=tenant_id,
    )
    return _Actor(
        tenant_id=tenant_id,
        actor_id=uuid.UUID(_pk_to_uuid4(user.pk)),
        token=_make_access_token(user),
    )


def _headers(actor: _Actor) -> dict:
    return {
        "HTTP_X_GATEWAY_TOKEN": GW_TOKEN,
        "HTTP_AUTHORIZATION": f"Bearer {actor.token}",
    }


def _mut_headers(actor: _Actor) -> dict:
    """Headers for mutating requests — includes Idempotency-Key (rule 5.3)."""
    return {
        **_headers(actor),
        "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
    }


@pytest.mark.django_db
class TestTasksPermissions:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def test_list_returns_200(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        resp = Client().get(BASE_URL, **_headers(actor))
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "next_cursor" in data

    def test_missing_gateway_token_returns_403(self):
        actor = _make_actor(uuid.uuid4())
        resp = Client().get(
            BASE_URL,
            HTTP_AUTHORIZATION=f"Bearer {actor.token}",
        )
        assert resp.status_code == 403

    def test_missing_authorization_returns_401(self):
        resp = Client().get(BASE_URL, HTTP_X_GATEWAY_TOKEN=GW_TOKEN)
        assert resp.status_code == 401


@pytest.mark.django_db
class TestTasksEnforced:
    @pytest.fixture(autouse=True)
    def _rbac_enforce(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "ENFORCE"
        settings.RBAC_POLICY_CACHE_TTL = 60

    def test_no_permission_returns_403(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        resp = Client().get(BASE_URL, **_headers(actor))
        assert resp.status_code == 403


@pytest.mark.django_db
class TestTaskCRUD:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def _create(self, actor: _Actor, **kwargs) -> dict:
        payload = {"title": "Test task", **kwargs}
        resp = Client().post(
            BASE_URL,
            data=payload,
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 201, resp.content
        return resp.json()

    def test_create_task_returns_201(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        data = self._create(actor)
        assert data["title"] == "Test task"
        assert data["status"] == "open"
        assert data["priority"] == "normal"
        assert data["created_by"] == str(actor.actor_id)

    @pytest.mark.django_db
    def test_create_task_with_all_fields(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        assignee_uuid = _make_actor(tenant_id).actor_id
        data = self._create(
            actor,
            title="Full task",
            description="Desc",
            priority="high",
            assigned_to_id=assignee_uuid,
            due_date="2026-12-31",
            entity_type="contract",
            entity_id=str(uuid.uuid4()),
        )
        assert data["priority"] == "high"
        assert data["assigned_to_id"] == str(assignee_uuid)
        assert data["due_date"] == "2026-12-31"

    def test_get_task_returns_200(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        created = self._create(actor)
        task_id = created["id"]

        resp = Client().get(f"{BASE_URL}{task_id}/", **_headers(actor))
        assert resp.status_code == 200
        assert resp.json()["id"] == task_id

    def test_get_task_wrong_tenant_returns_404(self):
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        actor_a = _make_actor(tenant_a)
        actor_b = _make_actor(tenant_b)
        created = self._create(actor_a)
        task_id = created["id"]

        resp = Client().get(f"{BASE_URL}{task_id}/", **_headers(actor_b))
        assert resp.status_code == 404

    def test_update_task_by_creator(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        created = self._create(actor)
        task_id = created["id"]

        resp = Client().patch(
            f"{BASE_URL}{task_id}/",
            data={"title": "Updated title"},
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated title"

    def test_update_task_by_non_creator_returns_403(self):
        tenant_id = uuid.uuid4()
        creator = _make_actor(tenant_id)
        other_actor = _make_actor(tenant_id)
        created = self._create(creator)
        task_id = created["id"]

        resp = Client().patch(
            f"{BASE_URL}{task_id}/",
            data={"title": "Hacked"},
            content_type="application/json",
            **_mut_headers(other_actor),
        )
        assert resp.status_code == 403


@pytest.mark.django_db
class TestTaskStateMachine:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def _create_task(self, actor: _Actor) -> dict:
        resp = Client().post(
            BASE_URL,
            data={"title": "SM task"},
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 201
        return resp.json()

    def test_open_to_in_progress(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        task = self._create_task(actor)

        resp = Client().post(
            f"{BASE_URL}{task['id']}/status/",
            data={"status": "in_progress"},
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

    def test_invalid_transition_returns_422(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        task = self._create_task(actor)

        # OPEN → DONE is not allowed (must go through IN_PROGRESS)
        resp = Client().post(
            f"{BASE_URL}{task['id']}/status/",
            data={"status": "done"},
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 422

    def test_cancel_task(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        task = self._create_task(actor)

        resp = Client().post(
            f"{BASE_URL}{task['id']}/cancel/",
            **_mut_headers(actor),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"
        assert data["closed_at"] is not None

    def test_double_cancel_returns_422(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        task = self._create_task(actor)
        Client().post(
            f"{BASE_URL}{task['id']}/cancel/",
            **_mut_headers(actor),
        )

        resp = Client().post(
            f"{BASE_URL}{task['id']}/cancel/",
            **_mut_headers(actor),
        )
        assert resp.status_code == 422


@pytest.mark.django_db
class TestTaskFileAttachment:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def _create_task(self, actor: _Actor) -> str:
        resp = Client().post(
            BASE_URL,
            data={"title": "File task"},
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 201
        return resp.json()["id"]

    def test_list_files_returns_attached(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        task_id = self._create_task(actor)
        file_id = uuid.uuid4()

        Client().post(
            f"{BASE_URL}{task_id}/files/",
            data={"file_id": str(file_id)},
            content_type="application/json",
            **_mut_headers(actor),
        )
        resp = Client().get(f"{BASE_URL}{task_id}/files/", **_headers(actor))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["file_id"] == str(file_id)
        assert data[0]["attached_by"] == str(actor.actor_id)

    def test_attach_and_detach_file(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        task_id = self._create_task(actor)
        file_id = uuid.uuid4()

        # attach
        resp = Client().post(
            f"{BASE_URL}{task_id}/files/",
            data={"file_id": str(file_id)},
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 201
        assert resp.json()["file_id"] == str(file_id)

        # detach
        resp = Client().delete(
            f"{BASE_URL}{task_id}/files/{file_id}/",
            **_mut_headers(actor),
        )
        assert resp.status_code == 204

    def test_detach_by_non_owner_returns_403(self):
        tenant_id = uuid.uuid4()
        owner = _make_actor(tenant_id)
        other_actor = _make_actor(tenant_id)
        task_id = self._create_task(owner)
        file_id = uuid.uuid4()

        Client().post(
            f"{BASE_URL}{task_id}/files/",
            data={"file_id": str(file_id)},
            content_type="application/json",
            **_mut_headers(owner),
        )
        resp = Client().delete(
            f"{BASE_URL}{task_id}/files/{file_id}/",
            **_mut_headers(other_actor),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "NOT_ATTACHMENT_OWNER"

    def test_attach_same_file_twice_returns_400(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        task_id = self._create_task(actor)
        file_id = uuid.uuid4()

        Client().post(
            f"{BASE_URL}{task_id}/files/",
            data={"file_id": str(file_id)},
            content_type="application/json",
            **_mut_headers(actor),
        )
        resp = Client().post(
            f"{BASE_URL}{task_id}/files/",
            data={"file_id": str(file_id)},
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestTaskPagination:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def test_cursor_pagination(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)

        for i in range(3):
            Client().post(
                BASE_URL,
                data={"title": f"Task {i}"},
                content_type="application/json",
                **_mut_headers(actor),
            )

        resp = Client().get(f"{BASE_URL}?limit=2", **_headers(actor))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["next_cursor"] is not None

        # Fetch next page
        cursor = data["next_cursor"]
        resp2 = Client().get(f"{BASE_URL}?limit=2&cursor={cursor}", **_headers(actor))
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert len(data2["items"]) == 1
        assert data2["next_cursor"] is None

    def test_tenant_isolation_in_list(self):
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        actor_a = _make_actor(tenant_a)
        actor_b = _make_actor(tenant_b)

        Client().post(
            BASE_URL,
            data={"title": "Task in A"},
            content_type="application/json",
            **_mut_headers(actor_a),
        )

        resp = Client().get(BASE_URL, **_headers(actor_b))
        assert resp.status_code == 200
        assert resp.json()["items"] == []


@pytest.mark.django_db
class TestTaskComments:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def _create_task(self, actor: _Actor) -> str:
        resp = Client().post(
            BASE_URL,
            data={"title": "Task for comments"},
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 201
        return resp.json()["id"]

    def test_add_and_list_comment(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        task_id = self._create_task(actor)

        resp = Client().post(
            f"{BASE_URL}{task_id}/comments/",
            data={"body": "Первый комментарий"},
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 201
        comment = resp.json()
        assert comment["body"] == "Первый комментарий"
        assert comment["author_id"] == str(actor.actor_id)

        resp = Client().get(f"{BASE_URL}{task_id}/comments/", **_headers(actor))
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["body"] == "Первый комментарий"

    def test_comment_on_cancelled_task_returns_422(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        task_id = self._create_task(actor)

        Client().post(
            f"{BASE_URL}{task_id}/cancel/",
            content_type="application/json",
            **_mut_headers(actor),
        )
        resp = Client().post(
            f"{BASE_URL}{task_id}/comments/",
            data={"body": "Нельзя"},
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 422

    def test_comment_blank_body_returns_422(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        task_id = self._create_task(actor)

        resp = Client().post(
            f"{BASE_URL}{task_id}/comments/",
            data={"body": "   "},
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 422

    def test_comments_tenant_isolated(self):
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        actor_a = _make_actor(tenant_a)
        actor_b = _make_actor(tenant_b)
        task_id = self._create_task(actor_a)

        Client().post(
            f"{BASE_URL}{task_id}/comments/",
            data={"body": "Comment from A"},
            content_type="application/json",
            **_mut_headers(actor_a),
        )
        resp = Client().get(
            f"{BASE_URL}{task_id}/comments/",
            **_headers(actor_b),
        )
        assert resp.status_code == 404


@pytest.mark.django_db
class TestTaskAssigneeFilter:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def test_list_filter_by_assigned_to(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        assignee_id = _make_actor(tenant_id).actor_id

        # Create task without assignee
        Client().post(
            BASE_URL,
            data={"title": "Unassigned"},
            content_type="application/json",
            **_mut_headers(actor),
        )
        # Create task with assignee
        Client().post(
            BASE_URL,
            data={"title": "Assigned", "assigned_to_id": str(assignee_id)},
            content_type="application/json",
            **_mut_headers(actor),
        )

        resp = Client().get(f"{BASE_URL}?assigned_to_id={assignee_id}", **_headers(actor))
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["title"] == "Assigned"
        assert items[0]["assigned_to_id"] == str(assignee_id)

    def test_create_with_assignee(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        assignee_id = _make_actor(tenant_id).actor_id

        resp = Client().post(
            BASE_URL,
            data={"title": "With assignee", "assigned_to_id": str(assignee_id)},
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 201
        assert resp.json()["assigned_to_id"] == str(assignee_id)

    def test_update_assignee(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        assignee_id = _make_actor(tenant_id).actor_id

        # Create
        resp = Client().post(
            BASE_URL,
            data={"title": "Update assignee"},
            content_type="application/json",
            **_mut_headers(actor),
        )
        task_id = resp.json()["id"]

        # Update assignee
        resp = Client().patch(
            f"{BASE_URL}{task_id}/",
            data={"assigned_to_id": str(assignee_id)},
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 200
        assert resp.json()["assigned_to_id"] == str(assignee_id)

    def test_unassign_via_update(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        assignee_id = _make_actor(tenant_id).actor_id

        # Create with assignee
        resp = Client().post(
            BASE_URL,
            data={"title": "Will unassign", "assigned_to_id": str(assignee_id)},
            content_type="application/json",
            **_mut_headers(actor),
        )
        task_id = resp.json()["id"]

        # Unassign
        resp = Client().patch(
            f"{BASE_URL}{task_id}/",
            data={"assigned_to_id": None},
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 200
        assert resp.json()["assigned_to_id"] is None
