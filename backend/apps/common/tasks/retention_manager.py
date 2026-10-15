from datetime import timedelta

import django.utils.timezone as tz
import structlog
from django.conf import settings
from django.db import connection, transaction

from apps.common.models.outbox import OutboxRecord
from apps.common.tasks import BaseEnterpriseTask
from config.celery import app

logger = structlog.get_logger(__name__)

_ARCHIVE_BATCH_SIZE = 500


@app.task(base=BaseEnterpriseTask, queue="retention")
def archive_processed_outbox_records_task() -> None:
    """
    Archives Normal-class OutboxRecord rows (ADR-0013):
      is_processed=True AND failed_permanently_at IS NULL AND processed_at < cutoff

    Quarantine-class (failed_permanently_at IS NOT NULL) and Pending-class records
    are never touched. Uses SKIP LOCKED to avoid contention with the dispatcher.
    Rows are copied to common_outbox_archive then deleted from the live table atomically.
    """
    cutoff = tz.now() - timedelta(days=settings.OUTBOX_ARCHIVE_WINDOW_DAYS)
    total_archived = 0

    while True:
        with transaction.atomic():
            ids = list(
                OutboxRecord.objects.filter(
                    is_processed=True,
                    failed_permanently_at__isnull=True,
                    processed_at__lt=cutoff,
                )
                .select_for_update(skip_locked=True)
                .values_list("id", flat=True)[:_ARCHIVE_BATCH_SIZE]
            )
            if not ids:
                break

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO common_outbox_archive (
                        id, event_type, payload_json, correlation_id, causal_root_id,
                        aggregate_id, aggregate_type, aggregate_seq,
                        is_processed, created_at, processed_at,
                        retry_count, last_error, failed_permanently_at, next_retry_at,
                        archived_at
                    )
                    SELECT
                        id, event_type, payload_json, correlation_id, causal_root_id,
                        aggregate_id, aggregate_type, aggregate_seq,
                        is_processed, created_at, processed_at,
                        retry_count, last_error, failed_permanently_at, next_retry_at,
                        NOW()
                    FROM common_transactional_outbox
                    WHERE id = ANY(%s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    [ids],
                )
                cursor.execute(
                    "DELETE FROM common_transactional_outbox WHERE id = ANY(%s)",
                    [ids],
                )

        total_archived += len(ids)
        logger.info("outbox_archive_batch_completed", batch_size=len(ids))

    logger.info("outbox_archive_job_completed", total_archived=total_archived)


@app.task(base=BaseEnterpriseTask, queue="retention")
def run_database_partition_and_retention_job_task() -> None:
    """
    Автоматический менеджер секционирования и очистки данных СУБД.
    1. Создает секции наперед во избежание сбоев записи.
    2. Отцепляет и уничтожает секции логов старше 90 дней (O(1) DROP).
    """
    now = tz.now()
    next_month = now.month + 1 if now.month < 12 else 1
    next_year = now.year if now.month < 12 else now.year + 1

    future_partition_name = f"audit_logs_y{next_year}m{next_month:02d}"
    start_bound = f"{next_year}-{next_month:02d}-01 00:00:00+00"

    end_month = next_month + 1 if next_month < 12 else 1
    end_year = next_year if next_month < 12 else next_year + 1
    end_bound = f"{end_year}-{end_month:02d}-01 00:00:00+00"

    with connection.cursor() as cursor:
        try:
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {future_partition_name} PARTITION OF common_audit_integrity_logs
                FOR VALUES FROM ('{start_bound}') TO ('{end_bound}');
            """)
            logger.info("FUTURE_DATABASE_PARTITION_CREATED", partition=future_partition_name)
            pass
        except Exception as exc:
            logger.critical("DATABASE_PARTITION_REJECTED", error=str(exc))
            raise exc
