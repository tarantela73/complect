from __future__ import annotations

import json
import uuid

import redis
import structlog

logger = structlog.get_logger(__name__)

_RESCAN_QUEUE_KEY = "files:rescan:queue"


class AdaptiveScanPolicy:
    """
    Redis LIST-backed rescan queue for files deferred by the ClamAV circuit breaker.

    Push contract: when scan returns CB_FAST_FAIL, the consumer quarantines
    the file with reason 'scan_deferred_cb_open' and calls push_rescan().
    Pop contract: the recovery scanner calls pop_rescan_batch() when the
    circuit is CLOSED and re-scans the deferred files.

    Queue ordering: FIFO (LPUSH + RPOP). No deduplication — the recovery
    scanner's mark_rescanning() CAS guards against double-processing.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)

    def push_rescan(self, *, file_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        payload = json.dumps({"file_id": str(file_id), "tenant_id": str(tenant_id)})
        self._redis.lpush(_RESCAN_QUEUE_KEY, payload)
        logger.info("files_rescan_enqueued", file_id=str(file_id))

    def pop_rescan_batch(self, n: int = 50) -> list[dict]:
        pipe = self._redis.pipeline()
        for _ in range(n):
            pipe.rpop(_RESCAN_QUEUE_KEY)
        items = []
        for raw in pipe.execute():
            if raw is None:
                break
            try:
                items.append(json.loads(raw))
            except (json.JSONDecodeError, ValueError):
                logger.error("files_rescan_queue_invalid_payload", raw=raw)
        return items

    def rescan_queue_size(self) -> int:
        return self._redis.llen(_RESCAN_QUEUE_KEY) or 0


def get_adaptive_scan_policy() -> AdaptiveScanPolicy:
    from django.conf import settings

    return AdaptiveScanPolicy(redis_url=settings.REDIS_CACHE_URL)
