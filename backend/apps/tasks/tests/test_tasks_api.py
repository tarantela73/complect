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

import pytest
from django.test import Client

GW_TOKEN = "complect_internal_secure_mesh_token_2026"
BASE_URL = "/api/v1/tasks/"


def _headers(tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    return {
        "HTTP_X_GATEWAY_TOKEN": GW_TOKEN,
        "HTTP_X_TENANT_ID": str(tenant_id),
        "HTTP_X_USER_ID": str(user_id),
    }


def _mut_headers(tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    """Headers for mutating requests — includes Idempotency-Key (rule 5.3)."""
    return {
        "HTTP_X_GATEWAY_TOKEN": GW_TOKEN,
        "HTTP_X_TENANT_ID": str(tenant_id),
        "HTTP_X_USER_ID": str(user_id),
        "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
    }


@pytest.mark.django_db
class TestTasksPermissions:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def test_list_returns_200(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        resp = Client().get(BASE_URL, **_headers(tenant_id, user_id))
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "next_cursor" in data

    def test_missing_gateway_token_returns_403(self):
        resp = Client().get(
            BASE_URL,
            HTTP_X_TENANT_ID=str(uuid.uuid4()),
            HTTP_X_USER_ID=str(uuid.uuid4()),
        )
        assert resp.status_code == 403

    def test_missing_context_headers_returns_401(self):
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
        user_id = uuid.uuid4()
        resp = Client().get(BASE_URL, **_headers(tenant_id, user_id))
        assert resp.status_code == 403


@pytest.mark.django_db
class TestTaskCRUD:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def _create(self, tenant_id: uuid.UUID, user_id: uuid.UUID, **kwargs) -> dict:
        payload = {"title": "Test task", **kwargs}
        resp = Client().post(
            BASE_URL,
            data=payload,
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 201, resp.content
        return resp.json()

    def test_create_task_returns_201(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        data = self._create(tenant_id, user_id)
        assert data["title"] == "Test task"
        assert data["status"] == "open"
        assert data["priority"] == "normal"
        assert data["created_by"] == str(user_id)

    @pytest.mark.django_db
    def test_create_task_with_all_fields(self):
        from apps.users.models import User
        from apps.users.services.auth_service import _pk_to_uuid4

        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        assignee = User.objects.create_user(
            username=f"assignee_{uuid.uuid4().hex[:8]}",
            email=f"assignee_{uuid.uuid4().hex[:8]}@example.com",
            password="Test123!",
            tenant_id=tenant_id,
        )
        assignee_uuid = _pk_to_uuid4(assignee.pk)
        data = self._create(
            tenant_id,
            user_id,
            title="Full task",
            description="Desc",
            priority="high",
            assigned_to_id=assignee_uuid,
            due_date="2026-12-31",
            entity_type="contract",
            entity_id=str(uuid.uuid4()),
        )
        assert data["priority"] == "high"
        assert data["assigned_to_id"] == assignee_uuid
        assert data["due_date"] == "2026-12-31"

    def test_get_task_returns_200(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        created = self._create(tenant_id, user_id)
        task_id = created["id"]

        resp = Client().get(f"{BASE_URL}{task_id}/", **_headers(tenant_id, user_id))
        assert resp.status_code == 200
        assert resp.json()["id"] == task_id

    def test_get_task_wrong_tenant_returns_404(self):
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        user_id = uuid.uuid4()
        created = self._create(tenant_a, user_id)
        task_id = created["id"]

        resp = Client().get(f"{BASE_URL}{task_id}/", **_headers(tenant_b, user_id))
        assert resp.status_code == 404

    def test_update_task_by_creator(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        created = self._create(tenant_id, user_id)
        task_id = created["id"]

        resp = Client().patch(
            f"{BASE_URL}{task_id}/",
            data={"title": "Updated title"},
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated title"

    def test_update_task_by_non_creator_returns_403(self):
        tenant_id = uuid.uuid4()
        creator_id = uuid.uuid4()
        other_user_id = uuid.uuid4()
        created = self._create(tenant_id, creator_id)
        task_id = created["id"]

        resp = Client().patch(
            f"{BASE_URL}{task_id}/",
            data={"title": "Hacked"},
            content_type="application/json",
            **_mut_headers(tenant_id, other_user_id),
        )
        assert resp.status_code == 403


@pytest.mark.django_db
class TestTaskStateMachine:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def _create_task(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        resp = Client().post(
            BASE_URL,
            data={"title": "SM task"},
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 201
        return resp.json()

    def test_open_to_in_progress(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        task = self._create_task(tenant_id, user_id)

        resp = Client().post(
            f"{BASE_URL}{task['id']}/status/",
            data={"status": "in_progress"},
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

    def test_invalid_transition_returns_422(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        task = self._create_task(tenant_id, user_id)

        # OPEN → DONE is not allowed (must go through IN_PROGRESS)
        resp = Client().post(
            f"{BASE_URL}{task['id']}/status/",
            data={"status": "done"},
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 422

    def test_cancel_task(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        task = self._create_task(tenant_id, user_id)

        resp = Client().post(
            f"{BASE_URL}{task['id']}/cancel/",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"
        assert data["closed_at"] is not None

    def test_double_cancel_returns_422(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        task = self._create_task(tenant_id, user_id)
        Client().post(
            f"{BASE_URL}{task['id']}/cancel/",
            **_mut_headers(tenant_id, user_id),
        )

        resp = Client().post(
            f"{BASE_URL}{task['id']}/cancel/",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 422


@pytest.mark.django_db
class TestTaskFileAttachment:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def _create_task(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
        resp = Client().post(
            BASE_URL,
            data={"title": "File task"},
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 201
        return resp.json()["id"]

    def test_list_files_returns_attached(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        task_id = self._create_task(tenant_id, user_id)
        file_id = uuid.uuid4()

        Client().post(
            f"{BASE_URL}{task_id}/files/",
            data={"file_id": str(file_id)},
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        resp = Client().get(f"{BASE_URL}{task_id}/files/", **_mut_headers(tenant_id, user_id))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["file_id"] == str(file_id)
        assert data[0]["attached_by"] == str(user_id)

    def test_attach_and_detach_file(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        task_id = self._create_task(tenant_id, user_id)
        file_id = uuid.uuid4()

        # attach
        resp = Client().post(
            f"{BASE_URL}{task_id}/files/",
            data={"file_id": str(file_id)},
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 201
        assert resp.json()["file_id"] == str(file_id)

        # detach
        resp = Client().delete(
            f"{BASE_URL}{task_id}/files/{file_id}/",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 204

    def test_detach_by_non_owner_returns_403(self):
        tenant_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        other_id = uuid.uuid4()
        task_id = self._create_task(tenant_id, owner_id)
        file_id = uuid.uuid4()

        Client().post(
            f"{BASE_URL}{task_id}/files/",
            data={"file_id": str(file_id)},
            content_type="application/json",
            **_mut_headers(tenant_id, owner_id),
        )
        resp = Client().delete(
            f"{BASE_URL}{task_id}/files/{file_id}/",
            **_mut_headers(tenant_id, other_id),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "NOT_ATTACHMENT_OWNER"

    def test_attach_same_file_twice_returns_400(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        task_id = self._create_task(tenant_id, user_id)
        file_id = uuid.uuid4()

        Client().post(
            f"{BASE_URL}{task_id}/files/",
            data={"file_id": str(file_id)},
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        resp = Client().post(
            f"{BASE_URL}{task_id}/files/",
            data={"file_id": str(file_id)},
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestTaskPagination:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def test_cursor_pagination(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()

        for i in range(3):
            Client().post(
                BASE_URL,
                data={"title": f"Task {i}"},
                content_type="application/json",
                **_mut_headers(tenant_id, user_id),
            )

        resp = Client().get(f"{BASE_URL}?limit=2", **_headers(tenant_id, user_id))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["next_cursor"] is not None

        # Fetch next page
        cursor = data["next_cursor"]
        resp2 = Client().get(f"{BASE_URL}?limit=2&cursor={cursor}", **_headers(tenant_id, user_id))
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert len(data2["items"]) == 1
        assert data2["next_cursor"] is None

    def test_tenant_isolation_in_list(self):
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        user_id = uuid.uuid4()

        Client().post(
            BASE_URL,
            data={"title": "Task in A"},
            content_type="application/json",
            **_mut_headers(tenant_a, user_id),
        )

        resp = Client().get(BASE_URL, **_headers(tenant_b, user_id))
        assert resp.status_code == 200
        assert resp.json()["items"] == []


@pytest.mark.django_db
class TestTaskComments:
    @pytest.fixture(autouse=True)
    def _rbac_off(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "OFF"

    def _create_task(self, tenant_id, user_id) -> str:
        resp = Client().post(
            BASE_URL,
            data={"title": "Task for comments"},
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 201
        return resp.json()["id"]

    def test_add_and_list_comment(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        task_id = self._create_task(tenant_id, user_id)

        resp = Client().post(
            f"{BASE_URL}{task_id}/comments/",
            data={"body": "Первый комментарий"},
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 201
        comment = resp.json()
        assert comment["body"] == "Первый комментарий"
        assert comment["author_id"] == str(user_id)

        resp = Client().get(f"{BASE_URL}{task_id}/comments/", **_headers(tenant_id, user_id))
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["body"] == "Первый комментарий"

    def test_comment_on_cancelled_task_returns_422(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        task_id = self._create_task(tenant_id, user_id)

        Client().post(
            f"{BASE_URL}{task_id}/cancel/",
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        resp = Client().post(
            f"{BASE_URL}{task_id}/comments/",
            data={"body": "Нельзя"},
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 422

    def test_comment_blank_body_returns_422(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        task_id = self._create_task(tenant_id, user_id)

        resp = Client().post(
            f"{BASE_URL}{task_id}/comments/",
            data={"body": "   "},
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 422

    def test_comments_tenant_isolated(self):
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        user_id = uuid.uuid4()
        task_id = self._create_task(tenant_a, user_id)

        Client().post(
            f"{BASE_URL}{task_id}/comments/",
            data={"body": "Comment from A"},
            content_type="application/json",
            **_mut_headers(tenant_a, user_id),
        )
        resp = Client().get(
            f"{BASE_URL}{task_id}/comments/",
            **_headers(tenant_b, user_id),
        )
        assert resp.status_code == 404
