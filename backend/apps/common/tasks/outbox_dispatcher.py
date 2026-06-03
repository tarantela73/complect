import json
import random
from datetime import timedelta

import django.utils.timezone as tz
import structlog
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q

from apps.common.models.outbox import OutboxRecord
from apps.common.services.outbox_service import _INFLIGHT_CACHE_KEY
from apps.common.tasks import BaseEnterpriseTask
from config.celery import app

logger = structlog.get_logger(__name__)

MAX_PUBLISH_RETRIES: int = getattr(settings, "OUTBOX_MAX_PUBLISH_RETRIES", 5)
_BACKOFF_BASE: int = getattr(settings, "OUTBOX_BACKOFF_BASE_SECONDS", 30)
_BACKOFF_CAP: int = getattr(settings, "OUTBOX_BACKOFF_MAX_SECONDS", 300)

# Mode A (ADR-0012): aggregate types that require serial dispatch.
# Events for these types are routed to per-slot streams processed by
# a single-concurrency worker — guarantees per-slot FIFO within a slot.
SERIAL_DISPATCH_AGGREGATE_TYPES: frozenset[str] = frozenset(
    getattr(settings, "SERIAL_DISPATCH_AGGREGATE_TYPES", {"finance.transaction", "finance.balance"})
)
_N_ROUTING_SLOTS: int = getattr(settings, "OUTBOX_SERIAL_ROUTING_SLOTS", 256)


def _routing_stream(record: OutboxRecord, tenant_id_str: str) -> str:
    if (
        record.aggregate_type in SERIAL_DISPATCH_AGGREGATE_TYPES
        and record.aggregate_id is not None
    ):
        slot = record.aggregate_id.int % _N_ROUTING_SLOTS
        return f"erp:streams:serial:{slot}"
    return f"erp:streams:tenant:{tenant_id_str}"

# Injectable clock — patch in tests to advance time without DB manipulation.
_clock = tz.now


def _next_retry_at(retry_count: int):
    """Exponential backoff with 10% jitter: min(base × 2^n, cap)."""
    delay = min(_BACKOFF_BASE * (2 ** retry_count), _BACKOFF_CAP)
    return _clock() + timedelta(seconds=delay + random.uniform(0, delay * 0.1))


@app.task(base=BaseEnterpriseTask, queue="outbox")
def run_outbox_polling_dispatcher_task() -> None:
    """
    Polling dispatcher for the Transactional Outbox pattern (ADR-0003).

    Primary trigger: transaction.on_commit hook in TransactionalOutboxService.
    Fallback trigger: Beat schedule every 10 seconds (covers orphaned records).

    Guarantees:
    - At-least-once delivery to Redis Streams (consumers must deduplicate on outbox_record_id).
    - Exponential backoff with jitter prevents retry storms during Redis outages.
    - SELECT FOR UPDATE SKIP LOCKED acquires locks before slicing — eliminates the
      TOCTOU gap of a lock-after-select pattern under concurrent workers.
    - Permanent failure after MAX_PUBLISH_RETRIES — record excluded from polling, not deleted.
    - Per-record error isolation — one stuck record does not block the batch.
    """
    now = _clock()
    try:
        with transaction.atomic():
            # Lock before slice: SELECT FOR UPDATE SKIP LOCKED in the initial query
            # prevents the double-selection window of lock-after-select. Concurrent
            # workers see already-locked rows and skip them via SKIP LOCKED, so there
            # is no overlap between batches and no wasted re-query per record.
            records = list(
                OutboxRecord.objects.select_for_update(skip_locked=True)
                .filter(
                    is_processed=False,
                    failed_permanently_at__isnull=True,
                )
                .filter(
                    Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now)
                )
                .order_by("created_at")[:100]
            )

            if not records:
                return

            redis_client = settings.REDIS_CLIENT

            for record in records:
                publish_error = None
                try:
                    tenant_id_str = str(record.payload_json.get("tenant_id", "global"))
                    stream_name = _routing_stream(record, tenant_id_str)
                    redis_client.xadd(
                        name=stream_name,
                        fields={
                            # Canonical deduplication key for consumers (at-least-once guarantee).
                            # Consumers MUST ignore events already processed by this ID.
                            "outbox_record_id": str(record.id),
                            # causal_root_id: stable logical root of the causal chain (ADR-0015).
                            # Equals outbox_record_id for normal events; differs for repair/compensation.
                            # Falls back to outbox_record_id for pre-ADR-0015 records (causal_root_id=NULL).
                            "causal_root_id": str(record.causal_root_id) if record.causal_root_id else str(record.id),
                            "event_type": record.event_type,
                            "correlation_id": str(record.correlation_id),
                            # Aggregate causal integrity fields (ADR-0012).
                            # aggregate_seq="" means pre-ADR-0012 record; consumer fence check skips.
                            "aggregate_id": str(record.aggregate_id) if record.aggregate_id else "",
                            "aggregate_type": record.aggregate_type or "",
                            "aggregate_seq": str(record.aggregate_seq) if record.aggregate_seq is not None else "",
                            "payload": json.dumps(record.payload_json),
                        },
                        id="*",
                        maxlen=10000,
                        approximate=True,
                    )
                except Exception as exc:
                    publish_error = exc

                if publish_error is None:
                    record.is_processed = True
                    record.processed_at = _clock()
                    record.next_retry_at = None
                    record.save(update_fields=["is_processed", "processed_at", "next_retry_at"])
                else:
                    new_retry_count = record.retry_count + 1
                    record.retry_count = new_retry_count
                    record.last_error = str(publish_error)
                    record.next_retry_at = _next_retry_at(new_retry_count)
                    update_fields = ["retry_count", "last_error", "next_retry_at"]

                    if new_retry_count >= MAX_PUBLISH_RETRIES:
                        record.failed_permanently_at = _clock()
                        update_fields.append("failed_permanently_at")
                        logger.error(
                            "outbox_record_permanently_failed",
                            record_id=str(record.id),
                            event_type=record.event_type,
                            retry_count=new_retry_count,
                            error=str(publish_error),
                        )
                    else:
                        logger.warning(
                            "outbox_record_publish_failed",
                            record_id=str(record.id),
                            event_type=record.event_type,
                            retry_count=new_retry_count,
                            error=str(publish_error),
                            next_retry_at=record.next_retry_at.isoformat(),
                        )

                    record.save(update_fields=update_fields)
    finally:
        # Release inflight guard so the next on_commit write can trigger a fresh wake-up.
        # This runs whether the task succeeded, failed, or raised unexpectedly.
        cache.delete(_INFLIGHT_CACHE_KEY)
