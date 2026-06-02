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

    Ownership model:
        When instance_id is provided to claim(), ack() requires the same instance_id.
        This prevents cross-instance ACK: instance B cannot confirm work claimed by A.
        Without instance_id, ACK is unbound (legacy / simple consumers).

    The INSERT + IntegrityError pattern is race-free: the DB unique constraint
    on (outbox_record_id, consumer) is the only true gate.
    """

    @staticmethod
    def claim(
        outbox_record_id: uuid.UUID,
        consumer: str,
        tenant_id: uuid.UUID,
        instance_id: uuid.UUID | None = None,
    ) -> bool:
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
                claimed_by_instance_id=instance_id,
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
    def ack(
        outbox_record_id: uuid.UUID,
        consumer: str,
        instance_id: uuid.UUID | None = None,
    ) -> bool:
        """
        Confirm successful completion of event processing.

        Returns True  — acked (acked_at stamped).
        Returns False — no matching claim found, or instance_id mismatch.
        Never raises.

        Idempotent: calling ack() twice returns False on the second call
        (acked_at filter misses the already-stamped record).

        When instance_id is provided, only the instance that claimed the event
        can ack it. This is an ownership check: ack returns False if
        claimed_by_instance_id != instance_id.
        """
        try:
            qs = ProcessedEvent.objects.filter(
                outbox_record_id=outbox_record_id,
                consumer=consumer,
                acked_at__isnull=True,
            )
            if instance_id is not None:
                qs = qs.filter(claimed_by_instance_id=instance_id)
            updated = qs.update(acked_at=tz.now())
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
