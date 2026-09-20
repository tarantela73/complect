"""
RBAC decorator for Django Ninja endpoints.

Usage:
    @router.get("/contracts/")
    @require_permission("read", "contracts")
    def list_contracts(request, ...):
        ...

The decorator authenticates through JWTAuth, including on permission-only routes.
RBAC uses its verified result only when request attributes and ContextVars agree.
Unauthenticated or inconsistent requests fail regardless of RBAC mode.

In ENFORCE mode PolicyEngine.check() raises PermissionDenied if the actor lacks the
required permission. In LOG_ONLY mode the check proceeds and denial is logged only.
Callers that want a 403 JSON envelope should catch PermissionDenied:

    except PermissionDenied:
        return 403, make_error_response("FORBIDDEN", "...")
"""

from __future__ import annotations

import asyncio
import functools
from typing import Callable

from asgiref.sync import sync_to_async
from django.core.exceptions import PermissionDenied

from apps.common.api.auth import jwt_auth, verified_request_context
from apps.common.security.policy_engine import PolicyEngine


def require_permission(action: str, resource: str) -> Callable:
    """
    Decorator factory. Enforces RBAC policy before the endpoint handler runs.

    Raises PermissionDenied if:
    - access authentication is missing or its request/context projection drifted
    - RBAC mode is ENFORCE and the actor lacks the required permission

    Supports both sync and async view functions.
    """

    def _check(request) -> None:
        if not jwt_auth(request):
            raise PermissionDenied("Authentication required")
        context = verified_request_context(request)
        if context is None:
            raise PermissionDenied("Authentication required")

        PolicyEngine.check(
            actor_id=context.actor_id,
            tenant_id=context.tenant_id,
            action=action,
            resource=resource,
        )

    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(request, *args, **kwargs):
                # _check runs a sync ORM query (RBAC cache miss → RoleBinding lookup);
                # must be offloaded to a thread to avoid SynchronousOnlyOperation.
                await sync_to_async(_check, thread_sensitive=True)(request)
                return await func(request, *args, **kwargs)

            async_wrapper._rbac_action = action
            async_wrapper._rbac_resource = resource
            return async_wrapper

        @functools.wraps(func)
        def wrapper(request, *args, **kwargs):
            _check(request)
            return func(request, *args, **kwargs)

        wrapper._rbac_action = action
        wrapper._rbac_resource = resource
        return wrapper

    return decorator
