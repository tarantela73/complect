from functools import lru_cache

import redis
from django.conf import settings


@lru_cache(maxsize=1)
def get_default_redis() -> redis.Redis:
    """Унифицированный кэш-клиент общего назначения (Fork-safe, string decoded)."""
    return redis.Redis.from_url(settings.REDIS_DEFAULT_URL, decode_responses=True)


@lru_cache(maxsize=1)
def get_streams_redis() -> redis.Redis:
    """Выделенный шинный клиент под Redis Streams Outbox диспетчеризацию."""
    return redis.Redis.from_url(settings.REDIS_STREAMS_URL, decode_responses=True)


@lru_cache(maxsize=1)
def get_idempotency_redis() -> redis.Redis:
    """Выделенный лок-клиент под защиту от дублирования транзакций Idempotency-Key."""
    return redis.Redis.from_url(settings.REDIS_IDEMPOTENCY_URL, decode_responses=True)


@lru_cache(maxsize=1)
def get_audit_redis() -> redis.Redis:
    """Изолированный клиент обслуживания криптографической цепи Audit Log Chain."""
    return redis.Redis.from_url(settings.REDIS_AUDIT_URL, decode_responses=True)
