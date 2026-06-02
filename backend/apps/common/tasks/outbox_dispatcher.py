import json
import random
from datetime import timedelta

import django.utils.timezone as tz
import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import Q

from apps.common.models.outbox import OutboxRecord
from apps.common.tasks import BaseEnterpriseTask
from config.celery import app

logger = structlog.get_logger(__name__)

MAX_PUBLISH_RETRIES: int = getattr(settings, "OUTBOX_MAX_PUBLISH_RETRIES", 5)
_BACKOFF_BASE: int = getattr(settings, "OUTBOX_BACKOFF_BASE_SECONDS", 30)
_BACKOFF_CAP: int = getattr(settings, "OUTBOX_BACKOFF_MAX_SECONDS", 300)


def _next_retry_at(retry_count: int):
    """Exponential backoff with 10% jitter: min(base × 2^n, cap)."""
    delay = min(_BACKOFF_BASE * (2 ** retry_count), _BACKOFF_CAP)
    return tz.now() + timedelta(seconds=delay + random.uniform(0, delay * 0.1))


@app.task(base=BaseEnterpriseTask, queue="outbox")
def run_outbox_polling_dispatcher_task() -> None:
    """
    Polling dispatcher for the Transactional Outbox pattern (ADR-0003).

    Guarantees:
    - At-least-once delivery to Redis Streams (consumers must deduplicate on outbox_record_id).
    - Exponential backoff with jitter prevents retry storms during Redis outages.
    - SELECT FOR UPDATE SKIP LOCKED prevents duplicate processing under concurrent workers.
    - Permanent failure after MAX_PUBLISH_RETRIES — record excluded from polling, not deleted.
    - Per-record error isolation — one stuck record does not block the batch.
    """
    now = tz.now()
    records = list(
        OutboxRecord.objects.filter(
            is_processed=False,
            failed_permanently_at__isnull=True,
        ).filter(
            Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now)
        ).order_by("created_at")[:100]
    )

    if not records:
        return

    redis_client = settings.REDIS_CLIENT

    for record in records:
        with transaction.atomic():
            locked_record = (
                OutboxRecord.objects.select_for_update(skip_locked=True)
                .filter(
                    id=record.id,
                    is_processed=False,
                    failed_permanently_at__isnull=True,
                )
                .filter(
                    Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now)
                )
                .first()
            )

            if not locked_record:
                continue

            try:
                tenant_id_str = str(locked_record.payload_json.get("tenant_id", "global"))
                stream_name = f"erp:streams:tenant:{tenant_id_str}"

                redis_client.xadd(
                    name=stream_name,
                    fields={
                        # Canonical deduplication key for consumers (at-least-once guarantee).
                        # Consumers MUST ignore events already processed by this ID.
                        "outbox_record_id": str(locked_record.id),
                        "event_type": locked_record.event_type,
                        "correlation_id": str(locked_record.correlation_id),
                        "payload": json.dumps(locked_record.payload_json),
                    },
                    id="*",
                    maxlen=10000,
                    approximate=True,
                )

                locked_record.is_processed = True
                locked_record.processed_at = tz.now()
                locked_record.next_retry_at = None
                locked_record.save(update_fields=["is_processed", "processed_at", "next_retry_at"])

            except Exception as exc:
                new_retry_count = locked_record.retry_count + 1
                locked_record.retry_count = new_retry_count
                locked_record.last_error = str(exc)
                locked_record.next_retry_at = _next_retry_at(new_retry_count)
                update_fields = ["retry_count", "last_error", "next_retry_at"]

                if new_retry_count >= MAX_PUBLISH_RETRIES:
                    locked_record.failed_permanently_at = tz.now()
                    update_fields.append("failed_permanently_at")
                    logger.error(
                        "outbox_record_permanently_failed",
                        record_id=str(locked_record.id),
                        event_type=locked_record.event_type,
                        retry_count=new_retry_count,
                        error=str(exc),
                    )
                else:
                    logger.warning(
                        "outbox_record_publish_failed",
                        record_id=str(locked_record.id),
                        event_type=locked_record.event_type,
                        retry_count=new_retry_count,
                        error=str(exc),
                        next_retry_at=locked_record.next_retry_at.isoformat(),
                    )

                locked_record.save(update_fields=update_fields)
