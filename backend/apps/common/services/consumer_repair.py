import uuid

import structlog

from apps.common.models.aggregate_sequence import AggregateSequenceFence
from apps.common.models.outbox import OutboxRecord
from apps.common.models.processed_event import ProcessedEvent

logger = structlog.get_logger(__name__)


class ConsumerRepairService:
    """
    Repair protocol for stuck consumer events (ADR-0013, Gap B).

    A stuck event is one where ProcessedEvent.acked_at IS NOT NULL but the
    corresponding business action was never executed (e.g., consumer crashed
    between ack() and the side-effect commit, or a pre-crash partial execution).

    Safety contract:
    - Only allowed for quarantine-class records (failed_permanently_at IS NOT NULL).
    - Audit log is mandatory — reset is operationally irreversible.
    - After reset, caller MUST immediately dispatch the repair task.
      A reset without dispatch leaves the event in limbo.
    - Finance domain: human sign-off required before calling this method.
    """

    @staticmethod
    def reset_for_repair(
        outbox_record_id: uuid.UUID,
        consumer: str,
        operator_id: str,
        reason: str,
        *,
        reset_fence: bool = False,
        tenant_id: uuid.UUID | None = None,
        aggregate_type: str | None = None,
        aggregate_id: uuid.UUID | None = None,
        target_seq: int | None = None,
    ) -> None:
        """
        Delete ProcessedEvent(s) for the given outbox_record_id + consumer,
        allowing re-processing via a fresh claim().

        reset_fence=True: also rewinds AggregateSequenceFence.last_seen_seq to
        target_seq - 1 so Mode B fence does not discard the re-dispatched event.
        Only relevant for snapshot-domain aggregates (Mode B). Finance/Mode A:
        ordering is guaranteed by serial stream, fence reset not needed.
        """
        record = OutboxRecord.objects.filter(id=outbox_record_id).first()
        if record is None:
            raise ValueError(f"OutboxRecord {outbox_record_id} not found or archived")
        if record.failed_permanently_at is None:
            raise ValueError(
                "reset_for_repair only allowed for quarantine-class records "
                "(failed_permanently_at IS NOT NULL). Use DLQ replay for consumer failures."
            )

        deleted, _ = ProcessedEvent.objects.filter(
            outbox_record_id=outbox_record_id,
            consumer=consumer,
        ).delete()

        if (
            reset_fence
            and tenant_id is not None
            and aggregate_type is not None
            and aggregate_id is not None
            and target_seq is not None
        ):
            AggregateSequenceFence.objects.filter(
                tenant_id=tenant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                consumer=consumer,
            ).update(last_seen_seq=max(0, target_seq - 1))

        logger.warning(
            "consumer_event_reset_for_repair",
            outbox_record_id=str(outbox_record_id),
            consumer=consumer,
            operator_id=operator_id,
            reason=reason,
            processed_events_deleted=deleted,
            fence_reset=reset_fence,
        )
