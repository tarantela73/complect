"""Release N HTTP boundary with the real users validator and PostgreSQL."""

import uuid
from unittest.mock import Mock

import jwt
import pyotp
import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.test import Client

from apps.common.context.core import ContextCore, clear_context
from apps.users.models import User
from apps.users.services.auth_service import _make_access_token, _pk_to_uuid4

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def actor():
    return User.objects.create_user(username="boundary-" + uuid.uuid4().hex, tenant_id=uuid.uuid4())


@pytest.fixture(autouse=True)
def clean_http_context():
    clear_context()
    yield
    clear_context()


@pytest.mark.parametrize("variant", ["mismatch", "disabled", "identity", "version", "epoch"])
def test_real_validator_rejects_before_rbac_and_business(actor, variant, monkeypatch):
    claims = jwt.decode(_make_access_token(actor), settings.SECRET_KEY, algorithms=["HS256"])
    if variant == "mismatch":
        claims["tenant_id"] = str(uuid.uuid4())
    elif variant == "disabled":
        User.objects.filter(pk=actor.pk).update(is_active=False)
    elif variant == "identity":
        claims["identity_id"] = str(uuid.uuid4())
    elif variant == "version":
        claims["security_version"] = 9999
    else:
        claims["credential_epoch"] = 9999
    policy, business = Mock(), Mock()
    monkeypatch.setattr("apps.common.api.permissions.PolicyEngine.check", policy)
    monkeypatch.setattr("apps.common.api.dashboard.get_cached_dashboard", business)
    response = Client().get(
        "/api/v1/dashboard/",
        headers={
            "Authorization": "Bearer " + jwt.encode(claims, settings.SECRET_KEY, algorithm="HS256"),
            "X-Gateway-Token": settings.EXPECTED_GATEWAY_TOKEN,
            "X-Tenant-ID": str(actor.tenant_id),
            "X-User-ID": _pk_to_uuid4(actor.pk),
        },
    )
    assert response.status_code == 401
    policy.assert_not_called()
    business.assert_not_called()
    assert ContextCore.tenant_id.get() is None
    assert ContextCore.user_id.get() is None


def test_valid_bearer_reaches_actual_permission_route_with_one_context(actor, monkeypatch):
    expected_actor = uuid.UUID(_pk_to_uuid4(actor.pk))
    seen = []

    def check(**kwargs):
        assert kwargs["actor_id"] == ContextCore.user_id.get() == expected_actor
        assert kwargs["tenant_id"] == ContextCore.tenant_id.get() == actor.tenant_id

    def business(tenant_id):
        assert tenant_id == ContextCore.tenant_id.get() == actor.tenant_id
        seen.append(tenant_id)
        # Stop before unrelated dashboard dependencies; the permission route was reached.
        raise PermissionDenied("boundary business probe")

    monkeypatch.setattr("apps.common.api.permissions.PolicyEngine.check", check)
    monkeypatch.setattr("apps.common.api.dashboard.get_cached_dashboard", business)
    spoofed = str(uuid.uuid4())
    response = Client().get(
        "/api/v1/dashboard/",
        headers={
            "Authorization": "Bearer " + _make_access_token(actor),
            "X-Gateway-Token": settings.EXPECTED_GATEWAY_TOKEN,
            "X-User-ID": spoofed,
            "X-Tenant-ID": spoofed,
            "X-CTX-User-ID": spoofed,
            "X-CTX-Tenant-ID": spoofed,
        },
    )
    assert response.status_code == 403
    assert seen == [actor.tenant_id]
    assert ContextCore.tenant_id.get() is None


@pytest.mark.parametrize(
    "path,body,error",
    [
        ("/api/v1/auth/login", {"username": "missing", "password": "missing"}, "INVALID_CREDENTIALS"),
        ("/api/v1/auth/refresh", {"refresh_token": "invalid"}, "REFRESH_TOKEN_INVALID"),
        ("/api/v1/auth/mfa/verify", {"totp_code": "123456"}, "MFA_TOKEN_MISSING"),
    ],
)
def test_public_auth_reaches_its_own_credential_flow(path, body, error):
    response = Client().post(
        path,
        body,
        content_type="application/json",
        headers={
            "X-Gateway-Token": settings.EXPECTED_GATEWAY_TOKEN,
            "X-User-ID": str(uuid.uuid4()),
            "X-CTX-Tenant-ID": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == error
    assert ContextCore.tenant_id.get() is None


def test_cors_preflight_remains_public():
    response = Client().options(
        "/api/v1/dashboard/",
        headers={
            "Origin": "http://localhost:8080",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response["Access-Control-Allow-Origin"] == "http://localhost:8080"


def test_public_api_query_uses_configured_tenant_without_actor(settings, monkeypatch):
    from apps.objects import api_public

    tenant = uuid.uuid4()
    settings.PUBLIC_SITE_TENANT_ID = str(tenant)
    query = api_public.get_public_object_by_slug
    seen = []

    def observed_query(slug):
        assert ContextCore.tenant_id.get() == tenant
        assert ContextCore.user_id.get() is None
        seen.append(tenant)
        return query(slug)

    monkeypatch.setattr(api_public, "get_public_object_by_slug", observed_query)
    response = Client().get(
        "/api/v1/public/objects/not-published/",
        headers={
            "Authorization": "Bearer invalid",
            "X-Tenant-ID": str(uuid.uuid4()),
            "X-User-ID": str(uuid.uuid4()),
            "X-CTX-Tenant-ID": str(uuid.uuid4()),
            "X-CTX-User-ID": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 404
    assert seen == [tenant]
    assert ContextCore.tenant_id.get() is None
    assert ContextCore.user_id.get() is None


def test_login_mfa_refresh_logout_keep_their_public_credential_contract():
    secret = pyotp.random_base32()
    user = User.objects.create_user(
        username="mfa-boundary-" + uuid.uuid4().hex,
        password="Boundary-test-password-123!",
        tenant_id=uuid.uuid4(),
        mfa_enabled=True,
        mfa_secret=secret,
    )
    client = Client(headers={"X-Gateway-Token": settings.EXPECTED_GATEWAY_TOKEN})
    login = client.post(
        "/api/v1/auth/login",
        {"username": user.username, "password": "Boundary-test-password-123!"},
        content_type="application/json",
    )
    assert login.status_code == 200
    assert login.json()["mfa_required"] is True
    partial = login.json()["access_token"]
    verified = client.post(
        "/api/v1/auth/mfa/verify",
        {"totp_code": pyotp.TOTP(secret).now()},
        content_type="application/json",
        headers={"Authorization": "Bearer " + partial},
    )
    assert verified.status_code == 200
    claims = jwt.decode(verified.json()["access_token"], settings.SECRET_KEY, algorithms=["HS256"])
    assert claims["type"] == "access"
    assert claims["user_id"] == _pk_to_uuid4(user.pk)
    assert claims["tenant_id"] == str(user.tenant_id)
    refresh = client.cookies["refresh_token"].value
    rotated = client.post("/api/v1/auth/refresh", {"refresh_token": refresh}, content_type="application/json")
    assert rotated.status_code == 200
    assert client.cookies["refresh_token"].value != refresh
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert ContextCore.tenant_id.get() is None
    assert ContextCore.user_id.get() is None
