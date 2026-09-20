"""RBAC permissions tests — tasks domain in ENFORCE mode."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from django.test import Client

from apps.common.models.rbac import Permission, Role, RoleBinding, RolePermission
from apps.tasks.services.task_service import TaskService
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
        username=f"task_rbac_user_{suffix}",
        email=f"task_rbac_user_{suffix}@example.com",
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
    return {
        **_headers(actor),
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
        actor = _make_actor(tenant_id)
        resp = Client().get(BASE_URL, **_headers(actor))
        assert resp.status_code == 403

    def test_list_with_read_permission_returns_200(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        _grant(actor.actor_id, tenant_id, "read", "tasks")
        resp = Client().get(BASE_URL, **_headers(actor))
        assert resp.status_code == 200

    def test_create_without_write_permission_returns_403(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        _grant(actor.actor_id, tenant_id, "read", "tasks")
        resp = Client().post(
            BASE_URL,
            data={"title": "New task"},
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 403

    def test_create_with_write_permission_returns_201(self):
        tenant_id = uuid.uuid4()
        actor = _make_actor(tenant_id)
        _grant(actor.actor_id, tenant_id, "write", "tasks")
        resp = Client().post(
            BASE_URL,
            data={"title": "New task"},
            content_type="application/json",
            **_mut_headers(actor),
        )
        assert resp.status_code == 201

    def test_delete_attachment_without_delete_permission_returns_403(self):
        tenant_id = uuid.uuid4()
        creator = _make_actor(tenant_id)
        _grant(creator.actor_id, tenant_id, "write", "tasks")
        task = _make_task(tenant_id, creator.actor_id)
        file_id = uuid.uuid4()

        other_actor = _make_actor(tenant_id)
        _grant(other_actor.actor_id, tenant_id, "read", "tasks")
        resp = Client().delete(
            f"{BASE_URL}{task.id}/files/{file_id}/",
            **_mut_headers(other_actor),
        )
        assert resp.status_code == 403
