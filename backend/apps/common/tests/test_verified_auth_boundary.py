"""Regression coverage at the HTTP middleware/permission boundary."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import jwt
import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.test import RequestFactory

from apps.common.api.permissions import require_permission
from apps.common.context.core import ContextCore, clear_context
from apps.common.contracts.auth_context import ValidatedAuthContext
from apps.common.tenancy.middleware import AsgiTenantEnforcementMiddleware


@pytest.fixture(autouse=True)
def clean_context():
    clear_context()
    yield
    clear_context()


def access_token(settings, actor, tenant, **claims):
    payload = {
        "user_id": str(actor),
        "tenant_id": str(tenant),
        "type": "access",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        **claims,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


@pytest.mark.parametrize("family", ["X-User-ID", "X-CTX-User-ID"])
def test_spoofed_headers_cannot_enter_permission_only_endpoint(family, settings, monkeypatch):
    settings.EXPECTED_GATEWAY_TOKEN = ""
    settings.PUBLIC_SITE_TENANT_ID = str(uuid.uuid4())
    policy = Mock()
    business = Mock()
    monkeypatch.setattr("apps.common.api.permissions.PolicyEngine.check", policy)

    @require_permission("read", "dashboard")
    def endpoint(request):
        business()
        return JsonResponse({"ok": True})

    request = RequestFactory().get(
        "/api/v1/dashboard/",
        headers={family: str(uuid.uuid4()), family.replace("User", "Tenant"): str(uuid.uuid4())},
    )
    response = AsgiTenantEnforcementMiddleware(endpoint)(request)

    assert response.status_code == 401
    policy.assert_not_called()
    business.assert_not_called()
    assert ContextCore.tenant_id.get() is None
    assert ContextCore.user_id.get() is None


def test_valid_bearer_is_the_only_source_of_all_contexts(settings, monkeypatch):
    settings.EXPECTED_GATEWAY_TOKEN = ""
    settings.PUBLIC_SITE_TENANT_ID = str(uuid.uuid4())
    actor, tenant, spoofed = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    def validate(claims):
        assert ContextCore.tenant_id.get() is None
        assert ContextCore.user_id.get() is None
        assert claims["tenant_id"] == str(tenant)
        return ValidatedAuthContext(actor_id=actor, tenant_id=tenant)

    validator = Mock(validate=Mock(side_effect=validate))
    monkeypatch.setattr("apps.common.api.auth.get_auth_context_validator", lambda: validator)
    policy = Mock()
    monkeypatch.setattr("apps.common.api.permissions.PolicyEngine.check", policy)

    @require_permission("read", "dashboard")
    def endpoint(request):
        assert request.jwt_user_id == ContextCore.user_id.get() == actor
        assert request.jwt_tenant_id == ContextCore.tenant_id.get() == tenant
        return JsonResponse({"ok": True})

    request = RequestFactory().get(
        "/api/v1/dashboard/",
        headers={
            "Authorization": "Bearer " + access_token(settings, actor, tenant),
            "X-User-ID": str(spoofed),
            "X-Tenant-ID": str(spoofed),
            "X-CTX-User-ID": str(spoofed),
            "X-CTX-Tenant-ID": str(spoofed),
        },
    )
    response = AsgiTenantEnforcementMiddleware(endpoint)(request)
    assert response.status_code == 200
    validator.validate.assert_called_once()
    policy.assert_called_once_with(actor_id=actor, tenant_id=tenant, action="read", resource="dashboard")
    assert ContextCore.tenant_id.get() is None
    assert ContextCore.user_id.get() is None


@pytest.mark.parametrize("variant", ["malformed", "unsigned", "tampered", "expired", "mfa", "refresh", "no-exp"])
def test_invalid_bearer_never_publishes_context(variant, settings, monkeypatch):
    settings.EXPECTED_GATEWAY_TOKEN = ""
    settings.PUBLIC_SITE_TENANT_ID = str(uuid.uuid4())
    actor, tenant = uuid.uuid4(), uuid.uuid4()
    token = access_token(settings, actor, tenant)
    payload = {"user_id": str(actor), "tenant_id": str(tenant), "type": "access"}
    if variant == "malformed":
        token = "not-a-jwt"
    elif variant == "unsigned":
        token = jwt.encode({**payload, "exp": 9999999999}, key="", algorithm="none")
    elif variant == "tampered":
        token = jwt.encode({**payload, "exp": 9999999999}, "wrong-signing-key-for-this-test-only", algorithm="HS256")
    elif variant == "expired":
        token = access_token(settings, actor, tenant, exp=1)
    elif variant in {"mfa", "refresh"}:
        token = access_token(settings, actor, tenant, type="mfa_pending" if variant == "mfa" else "refresh")
    else:
        token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
    validator = Mock()
    monkeypatch.setattr("apps.common.api.auth.get_auth_context_validator", lambda: validator)
    downstream = Mock()
    request = RequestFactory().get("/api/v1/contracts/", headers={"Authorization": "Bearer " + token})
    response = AsgiTenantEnforcementMiddleware(downstream)(request)
    assert response.status_code == 401
    validator.validate.assert_not_called()
    downstream.assert_not_called()
    assert ContextCore.tenant_id.get() is None
    assert ContextCore.user_id.get() is None
    assert not hasattr(request, "jwt_tenant_id")


@pytest.mark.parametrize("failure", [PermissionDenied("INVALID_AUTH_CONTEXT"), RuntimeError("validator unavailable")])
def test_validator_failure_is_closed_before_downstream(failure, settings, monkeypatch):
    settings.EXPECTED_GATEWAY_TOKEN = ""
    settings.PUBLIC_SITE_TENANT_ID = str(uuid.uuid4())
    validator = Mock(validate=Mock(side_effect=failure))
    monkeypatch.setattr("apps.common.api.auth.get_auth_context_validator", lambda: validator)
    downstream = Mock()
    request = RequestFactory().get(
        "/api/v1/contracts/", headers={"Authorization": "Bearer " + access_token(settings, uuid.uuid4(), uuid.uuid4())}
    )
    assert AsgiTenantEnforcementMiddleware(downstream)(request).status_code == 401
    downstream.assert_not_called()
    assert ContextCore.tenant_id.get() is None
    assert ContextCore.user_id.get() is None


def test_missing_validator_fails_closed(settings, monkeypatch):
    settings.EXPECTED_GATEWAY_TOKEN = ""
    settings.PUBLIC_SITE_TENANT_ID = str(uuid.uuid4())
    monkeypatch.setattr("apps.common.api.auth.get_auth_context_validator", lambda: None)
    downstream = Mock()
    request = RequestFactory().get(
        "/api/v1/contracts/", headers={"Authorization": "Bearer " + access_token(settings, uuid.uuid4(), uuid.uuid4())}
    )
    assert AsgiTenantEnforcementMiddleware(downstream)(request).status_code == 401
    downstream.assert_not_called()


@pytest.mark.parametrize("field", ["jwt_user_id", "jwt_tenant_id", "user_context", "tenant_context"])
def test_permission_rejects_context_drift_without_repair(field, settings, monkeypatch):
    from apps.common.api.auth import jwt_auth

    actor, tenant, changed = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    validator = Mock(validate=Mock(return_value=ValidatedAuthContext(actor, tenant)))
    monkeypatch.setattr("apps.common.api.auth.get_auth_context_validator", lambda: validator)
    request = RequestFactory().get("/", headers={"Authorization": "Bearer " + access_token(settings, actor, tenant)})
    assert jwt_auth(request)
    if field == "user_context":
        ContextCore.user_id.set(changed)
    elif field == "tenant_context":
        ContextCore.tenant_id.set(changed)
    else:
        setattr(request, field, changed)
    policy = Mock()
    monkeypatch.setattr("apps.common.api.permissions.PolicyEngine.check", policy)
    business = Mock()
    with pytest.raises(PermissionDenied):
        require_permission("read", "dashboard")(business)(request)
    policy.assert_not_called()
    business.assert_not_called()
    validator.validate.assert_called_once()


@pytest.mark.parametrize("raises", [False, True])
def test_sequential_requests_cleanup_including_exceptions(raises, settings, monkeypatch):
    settings.EXPECTED_GATEWAY_TOKEN = ""
    settings.PUBLIC_SITE_TENANT_ID = str(uuid.uuid4())

    def validate(claims):
        assert ContextCore.tenant_id.get() is None
        return ValidatedAuthContext(uuid.UUID(claims["user_id"]), uuid.UUID(claims["tenant_id"]))

    monkeypatch.setattr("apps.common.api.auth.get_auth_context_validator", lambda: Mock(validate=validate))
    for _ in range(2):
        actor, tenant = uuid.uuid4(), uuid.uuid4()

        def endpoint(request, actor=actor, tenant=tenant):
            assert request.jwt_tenant_id == ContextCore.tenant_id.get() == tenant
            assert request.jwt_user_id == ContextCore.user_id.get() == actor
            ContextCore.force_primary.set(True)
            if raises:
                raise RuntimeError("business failure")
            return JsonResponse({"ok": True})

        request = RequestFactory().get(
            "/api/v1/contracts/", headers={"Authorization": "Bearer " + access_token(settings, actor, tenant)}
        )
        middleware = AsgiTenantEnforcementMiddleware(endpoint)
        if raises:
            with pytest.raises(RuntimeError, match="business failure"):
                middleware(request)
        else:
            assert middleware(request).status_code == 200
        assert ContextCore.tenant_id.get() is None
        assert ContextCore.user_id.get() is None
        assert ContextCore.request_id.get() is None
        assert ContextCore.correlation_id.get() is None
        assert not ContextCore.force_primary.get()
        assert not hasattr(request, "jwt_user_id")


def test_concurrent_async_permission_context_isolation(settings, monkeypatch):
    def validate(claims):
        return ValidatedAuthContext(uuid.UUID(claims["user_id"]), uuid.UUID(claims["tenant_id"]))

    monkeypatch.setattr("apps.common.api.auth.get_auth_context_validator", lambda: Mock(validate=validate))
    policy_contexts = []

    def check(**kwargs):
        assert kwargs["tenant_id"] == ContextCore.tenant_id.get()
        assert kwargs["actor_id"] == ContextCore.user_id.get()
        policy_contexts.append(kwargs["tenant_id"])

    monkeypatch.setattr("apps.common.api.permissions.PolicyEngine.check", check)

    @require_permission("read", "dashboard")
    async def endpoint(request):
        await asyncio.sleep(0)
        assert request.jwt_tenant_id == ContextCore.tenant_id.get()
        assert request.jwt_user_id == ContextCore.user_id.get()
        return request.jwt_tenant_id

    async def one_request(actor, tenant):
        clear_context()
        request = RequestFactory().get(
            "/api/v1/dashboard/", headers={"Authorization": "Bearer " + access_token(settings, actor, tenant)}
        )
        try:
            return await endpoint(request)
        finally:
            clear_context()

    async def run():
        tenants = [uuid.uuid4(), uuid.uuid4()]
        result = await asyncio.gather(*(one_request(uuid.uuid4(), tenant) for tenant in tenants))
        assert result == tenants
        assert set(policy_contexts) == set(tenants)

    async_to_sync(run)()
    assert ContextCore.tenant_id.get() is None


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/mfa/verify",
        "/api/v1/docs",
        "/api/v1/openapi.json",
        "/health/",
        "/public/ping/",
        "/sitemap.xml",
        "/robots.txt",
    ],
)
def test_public_routes_do_not_adopt_spoofed_context(path, settings):
    settings.EXPECTED_GATEWAY_TOKEN = ""
    settings.PUBLIC_SITE_TENANT_ID = str(uuid.uuid4())

    def endpoint(request):
        assert ContextCore.tenant_id.get() is None
        assert ContextCore.user_id.get() is None
        assert not hasattr(request, "jwt_user_id")
        return JsonResponse({"ok": True})

    method = "post" if "/auth/" in path else "get"
    request = getattr(RequestFactory(), method)(
        path,
        headers={
            "Authorization": "Bearer invalid",
            "X-Tenant-ID": str(uuid.uuid4()),
            "X-CTX-User-ID": str(uuid.uuid4()),
        },
    )
    assert AsgiTenantEnforcementMiddleware(endpoint)(request).status_code == 200


def test_public_site_only_uses_configured_tenant(settings):
    tenant = uuid.uuid4()
    settings.PUBLIC_SITE_TENANT_ID = str(tenant)

    def endpoint(request):
        assert ContextCore.tenant_id.get() == tenant
        assert ContextCore.user_id.get() is None
        return JsonResponse({"ok": True})

    request = RequestFactory().get("/api/v1/public/objects/", headers={"X-Tenant-ID": str(uuid.uuid4())})
    assert AsgiTenantEnforcementMiddleware(endpoint)(request).status_code == 200
    assert ContextCore.tenant_id.get() is None


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/mfa/setup",
        "/api/v1/auth/mfa/confirm",
        "/api/v1/auth/merge-convergence/start",
        "/api/v1/auth/merge-convergence/complete",
        "/api/v1/auth/new-protected-route",
        "/api/v1/auth/login-extra",
        "/api/v1/docs-extra",
    ],
)
def test_auth_prefix_is_not_a_blanket_bypass(path, settings):
    settings.EXPECTED_GATEWAY_TOKEN = ""
    settings.PUBLIC_SITE_TENANT_ID = str(uuid.uuid4())
    downstream = Mock()
    request = RequestFactory().post(path)
    assert AsgiTenantEnforcementMiddleware(downstream)(request).status_code == 401
    downstream.assert_not_called()


def test_validator_is_registered_before_first_request():
    from apps.common.contracts.auth_context import get_auth_context_validator

    assert get_auth_context_validator() is not None
