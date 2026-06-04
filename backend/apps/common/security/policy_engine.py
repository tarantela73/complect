"""
RBAC PolicyEngine.

Single entry point for all authorization checks. Three enforcement modes:
  OFF       — bypass all checks (testing, migration period)
  LOG_ONLY  — evaluate but never block; denials logged as warnings (default rollout)
  ENFORCE   — raise PermissionDenied on denial

Mode is read from settings.RBAC_ENFORCEMENT_MODE. Can be toggled without redeploy
via settings change + worker restart. Per-tenant override via feature flag is intentional
future work — not implemented here.

Cache key: rbac:policy:{tenant_id}:{actor_id} → list of "action:resource" strings.
TTL: settings.RBAC_POLICY_CACHE_TTL (default 60s). Cache is invalidated explicitly on
RoleBinding mutations via PolicyEngine.invalidate().

Design invariant: PolicyEngine knows nothing about the causal graph. Authorization
denials are audit events, NOT causal incidents. They do not appear in OutboxRecord.
"""
from __future__ import annotations

import uuid

import structlog
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied

logger = structlog.get_logger(__name__)

_CACHE_PREFIX = "rbac:policy"


def _cache_key(tenant_id: uuid.UUID, actor_id: uuid.UUID) -> str:
    return f"{_CACHE_PREFIX}:{tenant_id}:{actor_id}"


def _load_permissions(actor_id: uuid.UUID, tenant_id: uuid.UUID) -> list[str]:
    from apps.common.models.rbac import RoleBinding

    bindings = (
        RoleBinding.objects.filter(
            user_id=actor_id,
            tenant_id=tenant_id,
            is_active=True,
        )
        .select_related("role")
        .prefetch_related("role__permissions")
    )

    return [
        f"{perm.action}:{perm.resource}"
        for binding in bindings
        for perm in binding.role.permissions.all()
    ]


class PolicyEngine:
    @staticmethod
    def check(
        *,
        actor_id: uuid.UUID,
        tenant_id: uuid.UUID,
        action: str,
        resource: str,
    ) -> bool:
        """
        Returns True if the actor has permission. In ENFORCE mode raises PermissionDenied
        on denial instead of returning False.

        OFF mode always returns True without any DB or cache access.
        """
        mode: str = getattr(settings, "RBAC_ENFORCEMENT_MODE", "OFF")

        if mode == "OFF":
            return True

        key = _cache_key(tenant_id, actor_id)
        ttl: int = getattr(settings, "RBAC_POLICY_CACHE_TTL", 60)

        cached = cache.get(key)
        if cached is None:
            cached = _load_permissions(actor_id, tenant_id)
            cache.set(key, cached, timeout=ttl)

        perm_key = f"{action}:{resource}"
        allowed = perm_key in cached

        if not allowed:
            logger.warning(
                "rbac_access_denied",
                actor_id=str(actor_id),
                tenant_id=str(tenant_id),
                action=action,
                resource=resource,
                mode=mode,
            )
            if mode == "ENFORCE":
                raise PermissionDenied(f"Permission denied: {perm_key}")

        return allowed

    @staticmethod
    def invalidate(*, tenant_id: uuid.UUID, actor_id: uuid.UUID) -> None:
        """Bust cached permission set for (tenant_id, actor_id). Call after any RoleBinding mutation."""
        cache.delete(_cache_key(tenant_id, actor_id))

    @staticmethod
    def invalidate_all_for_role(*, role_id: int) -> None:
        """
        Bust cache for all users currently bound to this role within any tenant.
        Needed when role permissions are mutated (not common in normal operation).
        """
        from apps.common.models.rbac import RoleBinding

        bindings = RoleBinding.objects.filter(role_id=role_id, is_active=True).values_list(
            "tenant_id", "user_id"
        )
        keys = [_cache_key(uuid.UUID(str(t)), uuid.UUID(str(u))) for t, u in bindings]
        if keys:
            cache.delete_many(keys)
