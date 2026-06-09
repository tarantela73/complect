"""
Admin API integration tests.

Tests the full HTTP stack: JWT auth → RBAC check → service → response.
"""
import json
import uuid

import pytest
from django.test import Client, override_settings

from apps.common.models.rbac import Permission, Role, RoleBinding, RolePermission
from apps.users.models import User
from apps.users.services.auth_service import _make_access_token, _pk_to_uuid4

TENANT_ID = uuid.uuid4()
GW_TOKEN = "complect_internal_secure_mesh_token_2026"


@pytest.fixture(autouse=True)
def seed_roles(db):
    for action, resource in [
        ("read", "users"), ("create", "users"), ("write", "users"),
        ("read", "contracts"),
    ]:
        Permission.objects.get_or_create(action=action, resource=resource)

    admin, _ = Role.objects.get_or_create(name="admin")
    manager, _ = Role.objects.get_or_create(name="manager")
    viewer, _ = Role.objects.get_or_create(name="viewer")

    for action, resource in [
        ("read", "users"), ("create", "users"), ("write", "users"), ("read", "contracts")
    ]:
        perm = Permission.objects.get(action=action, resource=resource)
        RolePermission.objects.get_or_create(role=admin, permission=perm)

    for action, resource in [("read", "users"), ("read", "contracts")]:
        perm = Permission.objects.get(action=action, resource=resource)
        RolePermission.objects.get_or_create(role=manager, permission=perm)
        RolePermission.objects.get_or_create(role=viewer, permission=perm)


def _make_user_with_role(role_name: str, tenant_id: uuid.UUID = TENANT_ID) -> tuple[User, str]:
    email = f"{role_name}_{uuid.uuid4().hex[:8]}@test.com"
    user = User.objects.create_user(
        username=email, email=email, password="Pass1!", tenant_id=tenant_id
    )
    role = Role.objects.get(name=role_name)
    RoleBinding.objects.create(
        tenant_id=tenant_id,
        user_id=uuid.UUID(_pk_to_uuid4(user.pk)),
        role=role,
    )
    return user, _make_access_token(user)


def _headers(user: User, token: str, mutating: bool = False) -> dict:
    """Build all headers needed to pass tenancy middleware + JWTAuth."""
    h = {
        "HTTP_AUTHORIZATION": f"Bearer {token}",
        "HTTP_X_GATEWAY_TOKEN": GW_TOKEN,
        "HTTP_X_TENANT_ID": str(user.tenant_id),
        "HTTP_X_USER_ID": _pk_to_uuid4(user.pk),
    }
    if mutating:
        h["HTTP_IDEMPOTENCY_KEY"] = str(uuid.uuid4())
    return h


def _gw_only() -> dict:
    return {"HTTP_X_GATEWAY_TOKEN": GW_TOKEN}


@pytest.mark.django_db(transaction=True)
class TestListUsers:
    def test_admin_can_list_users(self):
        user, token = _make_user_with_role("admin")
        resp = Client().get("/api/v1/admin/users/", **_headers(user, token))
        assert resp.status_code == 200
        assert "items" in resp.json()

    def test_viewer_can_list_users(self):
        user, token = _make_user_with_role("viewer")
        resp = Client().get("/api/v1/admin/users/", **_headers(user, token))
        assert resp.status_code == 200

    def test_unauthenticated_gets_401_or_403(self):
        resp = Client().get("/api/v1/admin/users/", **_gw_only())
        assert resp.status_code in (401, 403)


@pytest.mark.django_db(transaction=True)
class TestCreateUser:
    def test_admin_creates_user(self):
        user, token = _make_user_with_role("admin")
        resp = Client().post(
            "/api/v1/admin/users/",
            data=json.dumps({
                "email": "newuser@example.com",
                "full_name": "New User",
                "roles": ["viewer"],
                "send_invite_email": False,
            }),
            content_type="application/json",
            **_headers(user, token, mutating=True),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "newuser@example.com"
        assert "temp_password" in data

    @override_settings(RBAC_ENFORCEMENT_MODE="ENFORCE")
    def test_manager_cannot_create_user(self):
        user, token = _make_user_with_role("manager")
        resp = Client().post(
            "/api/v1/admin/users/",
            data=json.dumps({"email": "x@x.com", "full_name": "X", "roles": [], "send_invite_email": False}),
            content_type="application/json",
            **_headers(user, token, mutating=True),
        )
        assert resp.status_code == 403

    def test_duplicate_email_returns_422(self):
        user, token = _make_user_with_role("admin")
        payload = json.dumps({
            "email": "dup@example.com", "full_name": "Dup",
            "roles": [], "send_invite_email": False,
        })
        c = Client()
        first_headers = _headers(user, token, mutating=True)
        c.post("/api/v1/admin/users/", data=payload, content_type="application/json", **first_headers)
        second_headers = {**first_headers, "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}
        resp = c.post(
            "/api/v1/admin/users/", data=payload, content_type="application/json", **second_headers
        )
        assert resp.status_code == 422


@pytest.mark.django_db(transaction=True)
class TestDisableEnableUser:
    def test_admin_disables_user(self):
        admin, token = _make_user_with_role("admin")
        target, _ = _make_user_with_role("viewer")
        resp = Client().post(f"/api/v1/admin/users/{target.pk}/disable/", **_headers(admin, token, mutating=True))
        assert resp.status_code == 204
        target.refresh_from_db()
        assert not target.is_active

    def test_admin_enables_user(self):
        admin, token = _make_user_with_role("admin")
        target, _ = _make_user_with_role("viewer")
        target.is_active = False
        target.save()
        resp = Client().post(f"/api/v1/admin/users/{target.pk}/enable/", **_headers(admin, token, mutating=True))
        assert resp.status_code == 204
        target.refresh_from_db()
        assert target.is_active

    @override_settings(RBAC_ENFORCEMENT_MODE="ENFORCE")
    def test_manager_cannot_disable(self):
        manager_user, token = _make_user_with_role("manager")
        target, _ = _make_user_with_role("viewer")
        resp = Client().post(
            f"/api/v1/admin/users/{target.pk}/disable/",
            **_headers(manager_user, token, mutating=True),
        )
        assert resp.status_code == 403


@pytest.mark.django_db(transaction=True)
class TestAssignRoles:
    def test_admin_assigns_roles(self):
        admin, token = _make_user_with_role("admin")
        target, _ = _make_user_with_role("viewer")
        resp = Client().post(
            f"/api/v1/admin/users/{target.pk}/roles/",
            data=json.dumps({"roles": ["manager"]}),
            content_type="application/json",
            **_headers(admin, token, mutating=True),
        )
        assert resp.status_code == 200

    @override_settings(RBAC_ENFORCEMENT_MODE="ENFORCE")
    def test_manager_cannot_assign_roles(self):
        manager_user, token = _make_user_with_role("manager")
        target, _ = _make_user_with_role("viewer")
        resp = Client().post(
            f"/api/v1/admin/users/{target.pk}/roles/",
            data=json.dumps({"roles": ["manager"]}),
            content_type="application/json",
            **_headers(manager_user, token, mutating=True),
        )
        assert resp.status_code == 403


@pytest.mark.django_db(transaction=True)
class TestListRoles:
    def test_admin_can_list_roles(self):
        user, token = _make_user_with_role("admin")
        resp = Client().get("/api/v1/admin/roles/", **_headers(user, token))
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        role_names = {r["name"] for r in data["items"]}
        assert {"admin", "manager", "viewer"} <= role_names

    def test_viewer_can_list_roles(self):
        user, token = _make_user_with_role("viewer")
        resp = Client().get("/api/v1/admin/roles/", **_headers(user, token))
        assert resp.status_code == 200
