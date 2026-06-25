"""RBAC permissions tests — tasks domain in ENFORCE mode."""
from __future__ import annotations

import uuid

import pytest
from django.test import Client

from apps.common.models.rbac import Permission, Role, RoleBinding, RolePermission
from apps.tasks.services.task_service import TaskService

GW_TOKEN = "complect_internal_secure_mesh_token_2026"
BASE_URL = "/api/v1/tasks/"


def _headers(tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    return {
        "HTTP_X_GATEWAY_TOKEN": GW_TOKEN,
        "HTTP_X_TENANT_ID": str(tenant_id),
        "HTTP_X_USER_ID": str(user_id),
    }


def _mut_headers(tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    return {
        **_headers(tenant_id, user_id),
        "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
    }


def _grant(user_id: uuid.UUID, tenant_id: uuid.UUID, action: str, resource: str) -> None:
    perm, _ = Permission.objects.get_or_create(action=action, resource=resource)
    role_name = f"role_{action}_{resource}_{uuid.uuid4().hex[:6]}"
    role, _ = Role.objects.get_or_create(name=role_name)
    RolePermission.objects.get_or_create(role=role, permission=perm)
    RoleBinding.objects.get_or_create(
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        defaults={"is_active": True, "granted_by": None},
    )


def _make_task(tenant_id: uuid.UUID, created_by: uuid.UUID):
    return TaskService.create(
        tenant_id=tenant_id,
        created_by=created_by,
        title="RBAC test task",
    )


@pytest.mark.django_db
class TestTasksRBACEnforce:
    @pytest.fixture(autouse=True)
    def _enforce(self, settings):
        settings.RBAC_ENFORCEMENT_MODE = "ENFORCE"
        settings.RBAC_POLICY_CACHE_TTL = 60

    def test_list_without_permission_returns_403(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        resp = Client().get(BASE_URL, **_headers(tenant_id, user_id))
        assert resp.status_code == 403

    def test_list_with_read_permission_returns_200(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        _grant(user_id, tenant_id, "read", "tasks")
        resp = Client().get(BASE_URL, **_headers(tenant_id, user_id))
        assert resp.status_code == 200

    def test_create_without_write_permission_returns_403(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        _grant(user_id, tenant_id, "read", "tasks")
        resp = Client().post(
            BASE_URL,
            data={"title": "New task"},
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 403

    def test_create_with_write_permission_returns_201(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        _grant(user_id, tenant_id, "write", "tasks")
        resp = Client().post(
            BASE_URL,
            data={"title": "New task"},
            content_type="application/json",
            **_mut_headers(tenant_id, user_id),
        )
        assert resp.status_code == 201

    def test_delete_attachment_without_delete_permission_returns_403(self):
        tenant_id = uuid.uuid4()
        creator_id = uuid.uuid4()
        _grant(creator_id, tenant_id, "write", "tasks")
        task = _make_task(tenant_id, creator_id)
        file_id = uuid.uuid4()

        other_user = uuid.uuid4()
        _grant(other_user, tenant_id, "read", "tasks")
        resp = Client().delete(
            f"{BASE_URL}{task.id}/files/{file_id}/",
            **_mut_headers(tenant_id, other_user),
        )
        assert resp.status_code == 403
