import uuid

import structlog
from django.db import IntegrityError

from apps.common.models.processed_event import ProcessedEvent

logger = structlog.get_logger(__name__)


class ConsumerIdempotencyService:
    """
    Deduplication gate for at-least-once event consumers.

    Usage:
        if not ConsumerIdempotencyService.claim(outbox_record_id, consumer, tenant_id):
            return  # already processed — skip
        process_the_event()

    The INSERT + IntegrityError pattern is race-free: the DB unique constraint
    is the only true gate. get_or_create() would have a SELECT-then-INSERT gap.
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
