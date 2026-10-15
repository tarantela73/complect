"""
Contract test: every non-auth endpoint must be decorated with @require_permission.

This test walks Django Ninja's _router_registrations at import time (after config.urls
wires all domain routers) and asserts that every non-exempt operation carries the
_rbac_action marker set by require_permission().

Failure here means a new endpoint was added without RBAC coverage.
"""

from __future__ import annotations

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

# Prefixes whose endpoints establish identity rather than require it,
# or are intentionally anonymous (public site endpoints — ADR-0023).
EXEMPT_PREFIXES: tuple[str, ...] = ("/auth/", "/public/")


def _collect_operations():
    """Return list of (prefix, path, methods, view_func) for all registered operations."""
    import config.urls  # noqa: F401 — side-effect: wires routers onto api
    from config.api import api

    result = []
    for reg in api._router_registrations:
        prefix = reg[0]
        router = reg[1]
        for path, path_op in router.path_operations.items():
            for op in path_op.operations:
                result.append((prefix, path, frozenset(op.methods), op.view_func))
    return result


@pytest.mark.django_db
def test_all_non_auth_endpoints_have_rbac():
    """Every operation outside EXEMPT_PREFIXES must carry _rbac_action from require_permission."""
    operations = _collect_operations()
    assert operations, "No operations found — router wiring may have failed"

    unprotected = []
    for prefix, path, methods, fn in operations:
        if any(prefix.startswith(ep) for ep in EXEMPT_PREFIXES):
            continue
        if not hasattr(fn, "_rbac_action"):
            unprotected.append(f"{sorted(methods)} {prefix}{path} -> {fn.__name__}")

    assert not unprotected, "Endpoints missing @require_permission:\n" + "\n".join(
        f"  {e}" for e in sorted(unprotected)
    )


@pytest.mark.django_db
def test_all_rbac_permissions_are_registered_in_inventory():
    """Every (action, resource) pair used in @require_permission must exist in PERMISSIONS registry."""
    from apps.common.security.rbac_permissions import _BY_PAIR

    operations = _collect_operations()

    unknown = []
    seen: set[tuple[str, str]] = set()
    for _prefix, _path, _methods, fn in operations:
        if not hasattr(fn, "_rbac_action"):
            continue
        key = (fn._rbac_action, fn._rbac_resource)
        if key in seen:
            continue
        seen.add(key)
        if key not in _BY_PAIR:
            unknown.append(f"action={fn._rbac_action!r} resource={fn._rbac_resource!r} in {fn.__name__}")

    assert not unknown, "Permissions used in decorators but missing from rbac_permissions.PERMISSIONS:\n" + "\n".join(
        f"  {u}" for u in unknown
    )


def test_every_permission_endpoint_rejects_unverified_request_attributes(monkeypatch):
    """Exercise every registered permission wrapper, including auth=None routes."""
    import inspect
    import uuid
    from unittest.mock import Mock

    from apps.common.context.core import clear_context

    policy = Mock()
    monkeypatch.setattr("apps.common.api.permissions.PolicyEngine.check", policy)
    checked = 0
    for prefix, path, _methods, fn in _collect_operations():
        if not hasattr(fn, "_rbac_action"):
            continue
        clear_context()
        request = RequestFactory().get("/api/v1" + prefix + path)
        request.jwt_user_id = uuid.uuid4()
        request.jwt_tenant_id = uuid.uuid4()
        with pytest.raises(PermissionDenied):
            (async_to_sync(fn) if inspect.iscoroutinefunction(fn) else fn)(request)
        checked += 1
    assert checked > 0
    policy.assert_not_called()


def test_authentication_middleware_precedes_tenant_consumers(settings):
    middleware = settings.MIDDLEWARE
    boundary = middleware.index("apps.common.tenancy.middleware.AsgiTenantEnforcementMiddleware")
    for consumer in (
        "apps.common.middleware.idempotency.IdempotencyEnforcementMiddleware",
        "apps.common.middleware.lifecycle.read_your_own_writes.ReadYourOwnWritesStickyMiddleware",
        "apps.common.middleware.lifecycle.lifecycle.PostgresRlsEnforcementMiddleware",
    ):
        assert boundary < middleware.index(consumer)


def test_context_free_auth_allowlist_matches_explicit_public_operations():
    import config.urls  # noqa: F401
    from apps.common.tenancy.middleware import AsgiTenantEnforcementMiddleware
    from config.api import api

    public_auth = set()
    for prefix, router, *_ in api._router_registrations:
        if prefix != "/auth/":
            continue
        for path, path_op in router.path_operations.items():
            for op in path_op.operations:
                if not op.auth_callbacks:
                    assert op.methods == ["POST"]
                    public_auth.add("/api/v1/auth/" + path.lstrip("/"))
    assert public_auth == set(AsgiTenantEnforcementMiddleware.CONTEXT_FREE_AUTH_PATHS)
