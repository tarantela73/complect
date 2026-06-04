"""
Contract test: every non-auth endpoint must be decorated with @require_permission.

This test walks Django Ninja's _router_registrations at import time (after config.urls
wires all domain routers) and asserts that every non-exempt operation carries the
_rbac_action marker set by require_permission().

Failure here means a new endpoint was added without RBAC coverage.
"""
from __future__ import annotations

import pytest

# Prefixes whose endpoints establish identity rather than require it.
# These are intentionally exempt from require_permission.
EXEMPT_PREFIXES: tuple[str, ...] = ("/auth/",)


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

    assert not unprotected, (
        "Endpoints missing @require_permission:\n"
        + "\n".join(f"  {e}" for e in sorted(unprotected))
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

    assert not unknown, (
        "Permissions used in decorators but missing from rbac_permissions.PERMISSIONS:\n"
        + "\n".join(f"  {u}" for u in unknown)
    )
