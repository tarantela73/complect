"""
Runtime metric collectors for SLO evaluation and Ops Console.
Single source of truth — imported by both the ops API endpoint and the SLO task.
"""
from __future__ import annotations

import redis as redis_lib
import structlog
from django.conf import settings
from django.core.cache import cache
from django.db import connection

from apps.common.infra.redis import get_default_redis
from apps.common.models.outbox import OutboxRecord

logger = structlog.get_logger(__name__)

_CELERY_QUEUES = ("default", "outbox", "notifications", "finance", "files", "retention")
_DLQ_STREAM = "infrastructure:dlq:stream"


def collect_outbox_pending() -> int:
    return OutboxRecord.objects.filter(
        is_processed=False,
        failed_permanently_at__isnull=True,
    ).count()


def collect_outbox_failed() -> int:
    return OutboxRecord.objects.filter(
        failed_permanently_at__isnull=False,
    ).count()


def collect_outbox_lag_p95() -> float | None:
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
            return round(float(row[0]), 2)
    return None


def collect_outbox() -> dict[str, int | float | None]:
    pending = collect_outbox_pending()
    failed = collect_outbox_failed()
    lag_p95 = collect_outbox_lag_p95() if pending > 0 else None
    return {"pending": pending, "failed_permanently": failed, "lag_p95": lag_p95}


def collect_celery_queue_depths() -> dict[str, int]:
    try:
        client = redis_lib.Redis.from_url(settings.REDIS_CELERY_URL, decode_responses=True)
        return {q: client.llen(q) for q in _CELERY_QUEUES}
    except Exception:
        logger.warning("ops_celery_redis_unavailable")
        return {q: -1 for q in _CELERY_QUEUES}


def collect_circuit_breakers() -> dict[str, str]:
    return {"clamav": "OPEN" if cache.get("clamav:cb:open") else "CLOSED"}


def collect_dlq_count() -> int:
    try:
        return get_default_redis().xlen(_DLQ_STREAM)
    except Exception:
        logger.warning("ops_dlq_redis_unavailable")
        return -1


def collect_ws_connections() -> int:
    try:
        val = get_default_redis().get("ws:active_connections")
        return max(0, int(val)) if val is not None else 0
    except Exception:
        return 0


def collect_reconciliation_stats() -> dict[str, int]:
    try:
        from apps.payments.models import (  # noqa: PLC0415
            BankStatement,
            BankStatementLine,
            BankStatementStatus,
            MatchStatus,
        )
        importing = BankStatement.objects.filter(status=BankStatementStatus.IMPORTING).count()
        base = BankStatementLine.objects.filter(
            is_deleted=False,
            statement__status=BankStatementStatus.IMPORTED,
        )
        return {
            "statements_importing": importing,
            "unmatched_lines": base.filter(match_status=MatchStatus.UNMATCHED).count(),
            "ambiguous_lines": base.filter(match_status=MatchStatus.AMBIGUOUS).count(),
            "partial_lines":   base.filter(match_status=MatchStatus.PARTIAL).count(),
        }
    except Exception:
        logger.warning("ops_reconciliation_stats_unavailable")
        return {"statements_importing": -1, "unmatched_lines": -1, "ambiguous_lines": -1, "partial_lines": -1}


def collect_snapshot() -> dict:
    """Full snapshot dict for SLO evaluation."""
    outbox = collect_outbox()
    return {
        "outbox": outbox,
        "celery": collect_celery_queue_depths(),
        "dlq": collect_dlq_count(),
    }
