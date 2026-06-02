import json

import django.utils.timezone as tz
import structlog
from django.conf import settings
from django.db import transaction

from apps.common.models.outbox import OutboxRecord
from apps.common.tasks import BaseEnterpriseTask
from config.celery import app

logger = structlog.get_logger(__name__)


@app.task(base=BaseEnterpriseTask, queue="outbox")
def run_outbox_polling_dispatcher_task() -> None:
    """
    [ADR 0003 / ИСПРАВЛЕНО] SRE Поллинг-Диспетчер транзакционного Outbox.
    Пакетно выгребает необработанные события по композитному индексу outbox_dispatch_idx
    и атомарно пушит их в персистентные Redis Streams.
    """
    # Вытаскиваем пачку строго по нашему новому композитному индексу
    records = OutboxRecord.objects.filter(is_processed=False).order_by("created_at")[:100]

    if not records.exists():
        return

    redis_client = settings.REDIS_CLIENT

    for record in records:
        # Каждая строка обрабатывается в изолированной ACID транзакции СУБД [4.1]
        with transaction.atomic():
            # select_for_update(skip_locked=True) — SRE закон высоконагруженных очередей.
            # Исключает взаимные блокировки (Deadlocks) между параллельными воркерами Celery.
            locked_record = (
                OutboxRecord.objects.select_for_update(skip_locked=True)
                .filter(id=record.id, is_processed=False)
                .first()
            )

            if not locked_record:
                continue

            try:
                # Извлекаем tenant_id из сохраненного payload-слепка для маршрутизации стрима [Пункт 13]
                tenant_id_str = str(locked_record.payload_json.get("tenant_id", "global"))
                stream_name = f"erp:streams:tenant:{tenant_id_str}"

                # Пушим кадр в append-only Redis Stream лог вместо ненадежного Pub/Sub
                # maxlen=10000 гарантирует защиту RAM от переполнения
                redis_client.xadd(
                    name=stream_name,
                    fields={
                        "event_type": locked_record.event_type,
                        "correlation_id": str(locked_record.correlation_id),
                        "payload": json.dumps(locked_record.payload_json),
                    },
                    id="*",  # Автоматический таймстемп-инкремент sequence_id
                    maxlen=10000,
                    approximate=True,
                )

                # Переводим стейт в PostgreSQL СТРОГО ПОСЛЕ успешного ответа Redis-брокера [Пункт 12]
                locked_record.is_processed = True
                locked_record.processed_at = tz.now()
                locked_record.save(update_fields=["is_processed", "processed_at"])

            except Exception as exc:
                logger.error(
                    "OUTBOX_DISPATCHER_TRANSMISSION_FAILED",
                    record_id=str(locked_record.id),
                    event_type=locked_record.event_type,
                    error=str(exc),
                )
                # Транзакция откатится, строка останется в статусе is_processed=False для ретрая
                raise exc
