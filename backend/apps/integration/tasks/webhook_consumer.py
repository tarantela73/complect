from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta

import structlog
from django.db import transaction
from django.utils import timezone as tz

from apps.common.events.registry import ALL_DOMAIN_EVENT_TYPES
from apps.common.services.consumer_base import BaseDomainConsumer, StreamEvent
from apps.common.tasks import BaseEnterpriseTask
from apps.integration.services.webhook_delivery import RETRY_DELAYS, WebhookDeliveryError
from config.celery import app

logger = structlog.get_logger(__name__)


class WebhookFanoutConsumer(BaseDomainConsumer):
    """
    Fan-out consumer: for each delivered OutboxRecord, creates WebhookDelivery
    records for every active subscription matching the event type, then enqueues
    individual deliver_webhook_task calls.

    Design: read-only against the Outbox; all writes are isolated to the
    integration domain. Does NOT depend on the event payload structure —
    raw payload_json is forwarded verbatim to the subscriber.
    """

    consumer_name = "integration.webhook_fanout"
    ordering_mode = "FENCED"
    handled_event_types = ALL_DOMAIN_EVENT_TYPES

    def process(self, event: StreamEvent) -> None:
        from apps.integration.models import WebhookDelivery, WebhookDeliveryStatus, WebhookSubscription

        subscriptions = list(
            WebhookSubscription.objects.filter(
                tenant_id=event.tenant_id,
                is_active=True,
                # Django JSONField containment: any subscription whose event_types list
                # contains the current event_type string.
                event_types__contains=[event.event_type],
            )
        )

        if not subscriptions:
            return

        payload_bytes = json.dumps(event.payload, default=str).encode()
        payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
        event_id_raw = event.payload.get("event_id")
        try:
            event_id = uuid.UUID(str(event_id_raw))
        except (TypeError, ValueError):
            event_id = uuid.uuid4()

        with transaction.atomic():
            for sub in subscriptions:
                # Skip duplicates: same outbox_record + subscription already exists.
                if WebhookDelivery.objects.filter(
                    outbox_record_id=event.outbox_record_id,
                    subscription_id=sub.id,
                ).exists():
                    continue

                delivery = WebhookDelivery.objects.create(
                    tenant_id=event.tenant_id,
                    subscription=sub,
                    outbox_record_id=event.outbox_record_id,
                    event_type=event.event_type,
                    event_id=event_id,
                    status=WebhookDeliveryStatus.PENDING,
                    payload_sha256=payload_sha256,
                    attempt=1,
                )

                _delivery_id = delivery.id
                _payload = event.payload
                transaction.on_commit(
                    lambda did=_delivery_id, pl=_payload:
                        deliver_webhook_task.apply_async(
                            kwargs={"delivery_id": str(did), "payload": pl},
                            queue="integration",
                        )
                )

        logger.info(
            "webhook_fanout_dispatched",
            event_type=event.event_type,
            outbox_record_id=str(event.outbox_record_id),
            tenant_id=str(event.tenant_id),
            subscription_count=len(subscriptions),
        )


class _WebhookDeliveryTask(BaseEnterpriseTask):
    """Marks delivery DEAD before the base class writes to DLQ."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        delivery_id = kwargs.get("delivery_id")
        if delivery_id:
            try:
                from apps.integration.models import WebhookDelivery, WebhookDeliveryStatus
                WebhookDelivery.objects.filter(id=delivery_id).update(
                    status=WebhookDeliveryStatus.DEAD,
                )
            except Exception:
                pass
        super().on_failure(exc, task_id, args, kwargs, einfo)


@app.task(
    base=_WebhookDeliveryTask,
    name="apps.integration.tasks.webhook_consumer.deliver_webhook_task",
    queue="integration",
    ignore_result=True,
    max_retries=len(RETRY_DELAYS),
    acks_late=True,
)
def deliver_webhook_task(*, delivery_id: str, payload: dict | None = None) -> None:
    from apps.integration.models import WebhookDelivery, WebhookDeliveryStatus
    from apps.integration.services.webhook_delivery import WebhookDeliveryService

    did = uuid.UUID(delivery_id)

    try:
        WebhookDeliveryService.execute(did, payload_override=payload)
    except WebhookDeliveryError as exc:
        retries = deliver_webhook_task.request.retries

        if retries >= len(RETRY_DELAYS):
            # on_failure will mark DEAD and write to DLQ
            raise

        next_delay = RETRY_DELAYS[retries]
        next_retry_at = tz.now() + timedelta(seconds=next_delay)

        WebhookDelivery.objects.filter(id=did).update(
            status=WebhookDeliveryStatus.FAILED,
            attempt=retries + 2,  # attempt=1 on first run; retries=0 → attempt=2 on first retry
            next_retry_at=next_retry_at,
            error_detail=str(exc)[:2000],
        )

        logger.warning(
            "webhook_delivery_retry_scheduled",
            delivery_id=delivery_id,
            attempt=retries + 2,
            next_delay_s=next_delay,
        )

        raise deliver_webhook_task.retry(exc=exc, countdown=next_delay) from exc


@app.task(
    base=BaseEnterpriseTask,
    name="apps.integration.tasks.webhook_consumer.run_webhook_fanout_consumer_task",
    queue="integration",
    ignore_result=True,
)
def run_webhook_fanout_consumer_task() -> None:
    WebhookFanoutConsumer().run_batch(100)
