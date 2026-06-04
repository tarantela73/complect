import uuid

from django.core.cache import cache

DASHBOARD_CACHE_TTL = 30
_KEY = "dashboard:v1:tenant:{tenant_id}"


def _key(tenant_id: uuid.UUID) -> str:
    return _KEY.format(tenant_id=tenant_id)


def get_cached_dashboard(tenant_id: uuid.UUID) -> dict | None:
    return cache.get(_key(tenant_id))


def set_cached_dashboard(tenant_id: uuid.UUID, data: dict) -> None:
    cache.set(_key(tenant_id), data, DASHBOARD_CACHE_TTL)


def invalidate_dashboard(tenant_id: uuid.UUID) -> None:
    cache.delete(_key(tenant_id))
