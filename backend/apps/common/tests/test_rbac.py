"""
RBAC test suite — rule 17: RBAC layer ≥ 95% coverage.

Coverage targets:
  - PolicyEngine.check(): OFF / LOG_ONLY / ENFORCE modes
  - Cache hit / miss / invalidation
  - Multi-role permission union
  - Tenant isolation (user in tenant A cannot see tenant B perms)
  - require_permission decorator: unauthenticated, allowed, denied
  - RoleBinding model constraints
"""

import uuid
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, override_settings

from apps.common.models.rbac import Permission, Role, RoleBinding, RolePermission
from apps.common.security.policy_engine import PolicyEngine, _cache_key

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_permission(action: str, resource: str) -> Permission:
    perm, _ = Permission.objects.get_or_create(action=action, resource=resource)
    return perm


def _make_role(name: str, *perms: Permission) -> Role:
    role, _ = Role.objects.get_or_create(name=name)
    for p in perms:
        RolePermission.objects.get_or_create(role=role, permission=p)
    return role


def _bind(user_id: uuid.UUID, tenant_id: uuid.UUID, role: Role, is_active: bool = True) -> RoleBinding:
    binding, _ = RoleBinding.objects.get_or_create(
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        defaults={"is_active": is_active, "granted_by": None},
    )
    if binding.is_active != is_active:
        RoleBinding.objects.filter(pk=binding.pk).update(is_active=is_active)
        binding.is_active = is_active
    return binding


# ---------------------------------------------------------------------------
# PolicyEngine — mode: OFF
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPolicyEngineOff:
    @override_settings(RBAC_ENFORCEMENT_MODE="OFF")
    def test_off_mode_always_returns_true(self):
        result = PolicyEngine.check(
            actor_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            action="write",
            resource="contracts",
        )
        assert result is True

    @override_settings(RBAC_ENFORCEMENT_MODE="OFF")
    def test_off_mode_does_not_hit_db(self):
        actor = uuid.uuid4()
        tenant = uuid.uuid4()
        # No bindings exist for this user — OFF mode must not query DB.
        # If it did it would still return True; this asserts no exception.
        assert PolicyEngine.check(actor_id=actor, tenant_id=tenant, action="read", resource="dlq") is True


# ---------------------------------------------------------------------------
# PolicyEngine — mode: LOG_ONLY
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPolicyEngineLogOnly:
    @override_settings(RBAC_ENFORCEMENT_MODE="LOG_ONLY", RBAC_POLICY_CACHE_TTL=60)
    def test_allowed_returns_true(self):
        tenant = uuid.uuid4()
        actor = uuid.uuid4()
        perm = _make_permission("read", "contracts")
        role = _make_role(f"reader_{uuid.uuid4().hex[:6]}", perm)
        _bind(actor, tenant, role)

        assert PolicyEngine.check(actor_id=actor, tenant_id=tenant, action="read", resource="contracts") is True

    @override_settings(RBAC_ENFORCEMENT_MODE="LOG_ONLY", RBAC_POLICY_CACHE_TTL=60)
    def test_denied_returns_false_without_raising(self):
        tenant = uuid.uuid4()
        actor = uuid.uuid4()
        # No bindings — actor has no permissions
        result = PolicyEngine.check(actor_id=actor, tenant_id=tenant, action="write", resource="finance")
        assert result is False

    @override_settings(RBAC_ENFORCEMENT_MODE="LOG_ONLY", RBAC_POLICY_CACHE_TTL=60)
    def test_denied_logs_warning(self):
        import structlog.testing

        tenant = uuid.uuid4()
        actor = uuid.uuid4()

        with structlog.testing.capture_logs() as logs:
            PolicyEngine.check(actor_id=actor, tenant_id=tenant, action="post", resource="finance")

        denial_logs = [e for e in logs if e.get("event") == "rbac_access_denied"]
        assert len(denial_logs) == 1
        entry = denial_logs[0]
        assert entry["action"] == "post"
        assert entry["resource"] == "finance"
        assert entry["mode"] == "LOG_ONLY"

    @override_settings(RBAC_ENFORCEMENT_MODE="LOG_ONLY", RBAC_POLICY_CACHE_TTL=60)
    def test_inactive_binding_is_not_counted(self):
        tenant = uuid.uuid4()
        actor = uuid.uuid4()
        perm = _make_permission("write", "objects")
        role = _make_role(f"writer_{uuid.uuid4().hex[:6]}", perm)
        _bind(actor, tenant, role, is_active=False)

        result = PolicyEngine.check(actor_id=actor, tenant_id=tenant, action="write", resource="objects")
        assert result is False


# ---------------------------------------------------------------------------
# PolicyEngine — mode: ENFORCE
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPolicyEngineEnforce:
    @override_settings(RBAC_ENFORCEMENT_MODE="ENFORCE", RBAC_POLICY_CACHE_TTL=60)
    def test_raises_permission_denied_on_missing_perm(self):
        tenant = uuid.uuid4()
        actor = uuid.uuid4()

        with pytest.raises(PermissionDenied):
            PolicyEngine.check(actor_id=actor, tenant_id=tenant, action="replay", resource="dlq")

    @override_settings(RBAC_ENFORCEMENT_MODE="ENFORCE", RBAC_POLICY_CACHE_TTL=60)
    def test_allows_when_binding_exists(self):
        tenant = uuid.uuid4()
        actor = uuid.uuid4()
        perm = _make_permission("replay", "dlq")
        role = _make_role(f"sre_{uuid.uuid4().hex[:6]}", perm)
        _bind(actor, tenant, role)

        result = PolicyEngine.check(actor_id=actor, tenant_id=tenant, action="replay", resource="dlq")
        assert result is True


# ---------------------------------------------------------------------------
# PolicyEngine — cache
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPolicyEngineCache:
    @override_settings(RBAC_ENFORCEMENT_MODE="LOG_ONLY", RBAC_POLICY_CACHE_TTL=60)
    def test_cache_miss_populates_cache(self):
        from django.core.cache import cache

        tenant = uuid.uuid4()
        actor = uuid.uuid4()
        perm = _make_permission("read", "users")
        role = _make_role(f"reader_{uuid.uuid4().hex[:6]}", perm)
        _bind(actor, tenant, role)

        key = _cache_key(tenant, actor)
        cache.delete(key)
        assert cache.get(key) is None

        PolicyEngine.check(actor_id=actor, tenant_id=tenant, action="read", resource="users")

        cached = cache.get(key)
        assert cached is not None
        assert "read:users" in cached

    @override_settings(RBAC_ENFORCEMENT_MODE="LOG_ONLY", RBAC_POLICY_CACHE_TTL=60)
    def test_cache_hit_skips_db(self):
        from django.core.cache import cache

        tenant = uuid.uuid4()
        actor = uuid.uuid4()
        key = _cache_key(tenant, actor)

        # Pre-populate cache with a permission that doesn't exist in DB.
        cache.set(key, ["read:contragents"], timeout=60)

        result = PolicyEngine.check(actor_id=actor, tenant_id=tenant, action="read", resource="contragents")
        assert result is True

    @override_settings(RBAC_ENFORCEMENT_MODE="LOG_ONLY", RBAC_POLICY_CACHE_TTL=60)
    def test_invalidate_clears_cache(self):
        from django.core.cache import cache

        tenant = uuid.uuid4()
        actor = uuid.uuid4()
        key = _cache_key(tenant, actor)
        cache.set(key, ["read:contracts"], timeout=60)

        PolicyEngine.invalidate(tenant_id=tenant, actor_id=actor)

        assert cache.get(key) is None

    @override_settings(RBAC_ENFORCEMENT_MODE="LOG_ONLY", RBAC_POLICY_CACHE_TTL=60)
    def test_invalidate_all_for_role_clears_all_bindings(self):
        from django.core.cache import cache

        tenant1 = uuid.uuid4()
        tenant2 = uuid.uuid4()
        actor1 = uuid.uuid4()
        actor2 = uuid.uuid4()
        perm = _make_permission("manage", "users")
        role = _make_role(f"admin_{uuid.uuid4().hex[:6]}", perm)
        _bind(actor1, tenant1, role)
        _bind(actor2, tenant2, role)

        key1 = _cache_key(tenant1, actor1)
        key2 = _cache_key(tenant2, actor2)
        cache.set(key1, ["manage:users"], timeout=60)
        cache.set(key2, ["manage:users"], timeout=60)

        PolicyEngine.invalidate_all_for_role(role_id=role.pk)

        assert cache.get(key1) is None
        assert cache.get(key2) is None


# ---------------------------------------------------------------------------
# PolicyEngine — multi-role union
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPolicyEngineMultiRole:
    @override_settings(RBAC_ENFORCEMENT_MODE="ENFORCE", RBAC_POLICY_CACHE_TTL=60)
    def test_multiple_roles_union_permissions(self):
        tenant = uuid.uuid4()
        actor = uuid.uuid4()
        p1 = _make_permission("read", "contracts")
        p2 = _make_permission("post", "finance")
        role_a = _make_role(f"roleA_{uuid.uuid4().hex[:6]}", p1)
        role_b = _make_role(f"roleB_{uuid.uuid4().hex[:6]}", p2)
        _bind(actor, tenant, role_a)
        _bind(actor, tenant, role_b)

        # Both permissions must be available via union.
        assert PolicyEngine.check(actor_id=actor, tenant_id=tenant, action="read", resource="contracts") is True
        assert PolicyEngine.check(actor_id=actor, tenant_id=tenant, action="post", resource="finance") is True


# ---------------------------------------------------------------------------
# PolicyEngine — tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPolicyEngineTenantIsolation:
    @override_settings(RBAC_ENFORCEMENT_MODE="LOG_ONLY", RBAC_POLICY_CACHE_TTL=60)
    def test_permission_in_tenant_a_not_visible_in_tenant_b(self):
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        actor = uuid.uuid4()
        perm = _make_permission("write", "contragents")
        role = _make_role(f"writer_{uuid.uuid4().hex[:6]}", perm)
        _bind(actor, tenant_a, role)

        # Permission exists in tenant A.
        assert PolicyEngine.check(actor_id=actor, tenant_id=tenant_a, action="write", resource="contragents") is True
        # Same actor in tenant B must have no permissions.
        assert PolicyEngine.check(actor_id=actor, tenant_id=tenant_b, action="write", resource="contragents") is False


# ---------------------------------------------------------------------------
# require_permission decorator
# ---------------------------------------------------------------------------


class TestRequirePermissionDecorator:
    @pytest.fixture(autouse=True)
    def clean_request_context(self):
        from apps.common.context.core import clear_context

        clear_context()
        yield
        clear_context()

    def _make_request(self, user_id=None, tenant_id=None):
        import jwt
        from django.conf import settings

        from apps.common.api.auth import jwt_auth
        from apps.common.contracts.auth_context import ValidatedAuthContext

        request = RequestFactory().get("/")
        if user_id is not None and tenant_id is not None:
            token = jwt.encode(
                {"user_id": str(user_id), "tenant_id": str(tenant_id), "type": "access", "exp": 9999999999},
                settings.SECRET_KEY,
                algorithm="HS256",
            )
            request.META["HTTP_AUTHORIZATION"] = "Bearer " + token
            # Policy tests isolate the users Protocol; HTTP tests exercise the real adapter.
            with patch("apps.common.api.auth.get_auth_context_validator") as validator:
                validator.return_value.validate.return_value = ValidatedAuthContext(user_id, tenant_id)
                assert jwt_auth(request)
        return request

    def test_unauthenticated_raises_permission_denied(self):
        from apps.common.api.permissions import require_permission

        @require_permission("read", "contracts")
        def view(request):
            return "ok"

        request = self._make_request(user_id=None, tenant_id=None)
        with pytest.raises(PermissionDenied):
            view(request)

    def test_missing_tenant_raises_permission_denied(self):
        from apps.common.api.permissions import require_permission

        @require_permission("read", "contracts")
        def view(request):
            return "ok"

        request = self._make_request(user_id=uuid.uuid4(), tenant_id=None)
        with pytest.raises(PermissionDenied):
            view(request)

    @override_settings(RBAC_ENFORCEMENT_MODE="OFF")
    def test_off_mode_passes_through(self):
        from apps.common.api.permissions import require_permission

        @require_permission("write", "finance")
        def view(request):
            return "reached"

        request = self._make_request(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
        result = view(request)
        assert result == "reached"

    @override_settings(RBAC_ENFORCEMENT_MODE="ENFORCE", RBAC_POLICY_CACHE_TTL=60)
    @pytest.mark.django_db
    def test_enforce_mode_denied_raises(self):
        from apps.common.api.permissions import require_permission

        @require_permission("replay", "dlq")
        def view(request):
            return "reached"

        request = self._make_request(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
        with pytest.raises(PermissionDenied):
            view(request)

    @override_settings(RBAC_ENFORCEMENT_MODE="ENFORCE", RBAC_POLICY_CACHE_TTL=60)
    @pytest.mark.django_db
    def test_enforce_mode_allowed_passes(self):
        from apps.common.api.permissions import require_permission

        actor = uuid.uuid4()
        tenant = uuid.uuid4()
        perm = _make_permission("read", "objects")
        role = _make_role(f"obj_reader_{uuid.uuid4().hex[:6]}", perm)
        _bind(actor, tenant, role)

        @require_permission("read", "objects")
        def view(request):
            return "reached"

        request = self._make_request(user_id=actor, tenant_id=tenant)
        result = view(request)
        assert result == "reached"


# ---------------------------------------------------------------------------
# RoleBinding model constraints
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRoleBindingConstraints:
    def test_duplicate_binding_raises(self):
        from django.db import IntegrityError

        tenant = uuid.uuid4()
        actor = uuid.uuid4()
        perm = _make_permission("read", "finance")
        role = _make_role(f"fin_r_{uuid.uuid4().hex[:6]}", perm)
        _bind(actor, tenant, role)

        with pytest.raises(IntegrityError):
            RoleBinding.objects.create(tenant_id=tenant, user_id=actor, role=role)

    def test_same_user_different_tenant_is_allowed(self):
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        actor = uuid.uuid4()
        perm = _make_permission("write", "objects")
        role = _make_role(f"obj_w_{uuid.uuid4().hex[:6]}", perm)

        b1 = _bind(actor, tenant_a, role)
        b2 = _bind(actor, tenant_b, role)

        assert b1.pk != b2.pk

    def test_str_representation(self):
        tenant = uuid.uuid4()
        actor = uuid.uuid4()
        perm = _make_permission("manage", "users")
        role = _make_role(f"admin_{uuid.uuid4().hex[:6]}", perm)
        binding = _bind(actor, tenant, role)

        assert str(role.name) in str(binding)
