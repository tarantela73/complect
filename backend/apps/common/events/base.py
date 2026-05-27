import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from django.db import models, transaction
import json
import redis
from django.conf import settings


@dataclass(slots=True, frozen=True)
class ContractClosedEvent:
    """Иммутабельный типизированный контракт события для предотвращения Event Chaos."""

    event_id: uuid.UUID
    correlation_id: uuid.UUID
    occurred_at: datetime
    contract_id: uuid.UUID
    user_id: uuid.UUID
    remaining_amount: int
    event_version: int = 1


class OutboxRecord(models.Model):
    """Таблица транзакционной фиксации событий в рамках одной бизнес-транзакции СУБД."""

    id = models.BigAutoField(primary_key=True)
    event_id = models.UUIDField(default=uuid.uuid4, unique=True)
    event_type = models.CharField(max_length=255)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_processed = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "common_outbox"


class OutboxDispatcher:
    """Публикатор событий. Работает строго на фазе transaction.on_commit()."""

    @staticmethod
    def dispatch_event(event_type: str, event_data: dict):
        record = OutboxRecord.objects.create(event_type=event_type, payload=event_data)

        def _publish():
            r = redis.Redis.from_url(settings.REDIS_URL)
            # Пушим в Pub/Sub шину живых событий и Celery очередь
            r.publish(
                "stream:domain_events",
                json.dumps({"type": event_type, "payload": event_data}),
            )
            record.is_processed = True
            record.save(update_fields=["is_processed"])

        transaction.on_commit(_publish)


class IntegrationInbox(models.Model):
    """Изолированный Inbox Pattern слой для защиты от дублирования входящих сообщений от 1С/ЕСИА."""

    external_event_id = models.CharField(max_length=255, primary_key=True)
    provider = models.CharField(max_length=50, db_index=True)  # e.g., '1C', 'ESIA'
    payload = models.JSONField()
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "integration_inbox"
