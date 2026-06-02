import uuid

import structlog
from django.db import IntegrityError
from django.utils import timezone as tz

from apps.common.models.processed_event import ProcessedEvent

logger = structlog.get_logger(__name__)


class ConsumerIdempotencyService:
    """
    Distributed delivery guarantee for at-least-once event consumers.

    Lifecycle:
        claimed  → claim() returns True, acked_at=NULL  (processing started)
        acked    → ack() stamps acked_at                 (processing confirmed)
        duplicate→ claim() returns False                 (skip, already claimed)

    The INSERT + IntegrityError pattern is race-free: the DB unique constraint
    on (outbox_record_id, consumer) is the only true gate.
    """

    @staticmethod
    def claim(outbox_record_id: uuid.UUID, consumer: str, tenant_id: uuid.UUID) -> bool:
        """
        Attempt to claim processing rights for an event.

        Returns True  — first claim: proceed with processing.
        Returns False — already claimed: skip (idempotent).
        Never raises.
        """
        try:
            ProcessedEvent.objects.create(
                outbox_record_id=outbox_record_id,
                consumer=consumer,
                tenant_id=tenant_id,
            )
            return True
        except IntegrityError:
            logger.debug(
                "consumer_event_already_processed",
                outbox_record_id=str(outbox_record_id),
                consumer=consumer,
            )
            return False

    @staticmethod
    def ack(outbox_record_id: uuid.UUID, consumer: str) -> bool:
        """
        Confirm successful completion of event processing.

        Returns True  — acked (acked_at stamped).
        Returns False — no matching claim found (unknown event/consumer pair).
        Never raises.

        Idempotent: calling ack() twice on the same pair is a no-op on the
        second call (acked_at already set, updated_count=0 → returns False).
        """
        try:
            updated = ProcessedEvent.objects.filter(
                outbox_record_id=outbox_record_id,
                consumer=consumer,
                acked_at__isnull=True,
            ).update(acked_at=tz.now())
            if updated:
                logger.debug(
                    "consumer_event_acked",
                    outbox_record_id=str(outbox_record_id),
                    consumer=consumer,
                )
            return bool(updated)
        except Exception as exc:
            logger.error(
                "consumer_event_ack_failed",
                outbox_record_id=str(outbox_record_id),
                consumer=consumer,
                error=str(exc),
            )
            return False
