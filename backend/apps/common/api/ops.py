"""
Ops Console — pure read-only aggregator over existing infrastructure.
No domain logic, no tenant scope. Cross-tenant by design (platform admin only).
"""
from __future__ import annotations

import redis as redis_lib
import structlog
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import HttpRequest
from django.utils import timezone as tz
from ninja import Router, Schema

from apps.common.api.permissions import require_permission
from apps.common.infra.redis import get_default_redis
from apps.common.models.outbox import OutboxRecord

logger = structlog.get_logger(__name__)
router = Router(tags=["ops"])

_CELERY_QUEUES = ("default", "outbox", "notifications", "finance", "files", "retention")
_DLQ_STREAM = "infrastructure:dlq:stream"


class OutboxStats(Schema):
    pending: int
    failed_permanently: int
    lag_seconds_p95: float | None = None


class OpsSnapshot(Schema):
    outbox: OutboxStats
    celery: dict[str, int]
    circuit_breakers: dict[str, str]
    dlq: int
    ws_connections: int
    collected_at: str


def _outbox_stats() -> OutboxStats:
    pending = OutboxRecord.objects.filter(
        is_processed=False,
        failed_permanently_at__isnull=True,
    ).count()
    failed = OutboxRecord.objects.filter(
        failed_permanently_at__isnull=False,
    ).count()

    lag_p95: float | None = None
    if pending > 0:
        with connection.cursor() as cur:
            cur.execute("""
                SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (NOW() - created_at))
                )
                FROM common_transactional_outbox
                WHERE is_processed = FALSE AND failed_permanently_at IS NULL
            """)
            row = cur.fetchone()
            if row and row[0] is not None:
                lag_p95 = round(float(row[0]), 2)

    return OutboxStats(pending=pending, failed_permanently=failed, lag_seconds_p95=lag_p95)


def _celery_queue_depths() -> dict[str, int]:
    try:
        client = redis_lib.Redis.from_url(settings.REDIS_CELERY_URL, decode_responses=True)
        return {q: client.llen(q) for q in _CELERY_QUEUES}
    except Exception:
        logger.warning("ops_celery_redis_unavailable")
        return {q: -1 for q in _CELERY_QUEUES}


def _circuit_breaker_states() -> dict[str, str]:
    return {
        "clamav": "OPEN" if cache.get("clamav:cb:open") else "CLOSED",
    }


def _dlq_count() -> int:
    try:
        return get_default_redis().xlen(_DLQ_STREAM)
    except Exception:
        logger.warning("ops_dlq_redis_unavailable")
        return -1


def _ws_connections() -> int:
    try:
        val = get_default_redis().get("ws:active_connections")
        return max(0, int(val)) if val is not None else 0
    except Exception:
        return 0


@router.get("/", response={200: OpsSnapshot, 403: dict})
@require_permission("read", "ops")
def get_ops_snapshot(request: HttpRequest):
    return 200, OpsSnapshot(
        outbox=_outbox_stats(),
        celery=_celery_queue_depths(),
        circuit_breakers=_circuit_breaker_states(),
        dlq=_dlq_count(),
        ws_connections=_ws_connections(),
        collected_at=tz.now().isoformat(),
    )
