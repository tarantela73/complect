"""
RBAC decorator for Django Ninja endpoints.

Usage:
    @router.get("/contracts/")
    @require_permission("read", "contracts")
    def list_contracts(request, ...):
        ...

The decorator reads actor_id and tenant_id from request attributes set by JWTAuth
(request.jwt_user_id, request.jwt_tenant_id). Unauthenticated requests (missing attrs)
raise PermissionDenied immediately regardless of RBAC mode.

In ENFORCE mode PolicyEngine.check() raises PermissionDenied if the actor lacks the
required permission. In LOG_ONLY mode the check proceeds and denial is logged only.
Callers that want a 403 JSON envelope should catch PermissionDenied:

    except PermissionDenied:
        return 403, make_error_response("FORBIDDEN", "...")
"""
from __future__ import annotations

import functools
import uuid
from typing import Callable

from django.core.exceptions import PermissionDenied

from apps.common.security.policy_engine import PolicyEngine


def require_permission(action: str, resource: str) -> Callable:
    """
    Decorator factory. Enforces RBAC policy before the endpoint handler runs.

    Raises PermissionDenied if:
    - request has no jwt_user_id / jwt_tenant_id (unauthenticated)
    - RBAC mode is ENFORCE and the actor lacks the required permission
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(request, *args, **kwargs):
            actor_id: uuid.UUID | None = getattr(request, "jwt_user_id", None)
            tenant_id: uuid.UUID | None = getattr(request, "jwt_tenant_id", None)

            if actor_id is None or tenant_id is None:
                raise PermissionDenied("Authentication required")

            PolicyEngine.check(
                actor_id=actor_id,
                tenant_id=tenant_id,
                action=action,
                resource=resource,
            )
            return func(request, *args, **kwargs)

        wrapper._rbac_action = action
        wrapper._rbac_resource = resource
        return wrapper

    return decorator
