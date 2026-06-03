import uuid

import structlog
from django.db import connection
from django.utils import timezone as tz

from apps.common.models.aggregate_sequence import AggregateSequenceFence

logger = structlog.get_logger(__name__)


class AggregateSequenceAllocator:
    """
    Monotonic sequence allocator for aggregate causal integrity (ADR-0012).

    allocate() MUST be called inside the same DB transaction as the business
    operation and OutboxRecord insert. If the outer transaction rolls back,
    the increment rolls back too — no committed gaps possible.

    Implementation: INSERT ... ON CONFLICT DO UPDATE RETURNING is a single
    atomic statement with row-level lock; no MAX() scan, no phantom reads.
    """

    @staticmethod
    def allocate(
        tenant_id: uuid.UUID,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
    ) -> int:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO common_aggregate_sequences
                    (tenant_id, aggregate_type, aggregate_id, current_seq)
                VALUES (%s, %s, %s, 1)
                ON CONFLICT (tenant_id, aggregate_type, aggregate_id)
                DO UPDATE SET current_seq = common_aggregate_sequences.current_seq + 1
                RETURNING current_seq
                """,
                [str(tenant_id), aggregate_type, str(aggregate_id)],
            )
            row = cursor.fetchone()
        seq: int = row[0]
        logger.debug(
            "aggregate_seq_allocated",
            tenant_id=str(tenant_id),
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            seq=seq,
        )
        return seq


class AggregateSequenceFenceService:
    """
    Monotonic fence guard for Mode B (snapshot) consumers (ADR-0012).

    advance() atomically advances last_seen_seq and returns True when the
    event should be processed. Returns False when the event is stale
    (seq <= last_seen_seq) — the caller MUST discard without processing.

    Race semantics: two concurrent workers delivering the same event_seq —
    the first UPDATE wins (last_seen_seq advances); the second finds no row
    satisfying last_seen_seq__lt=event_seq → falls through to get_or_create
    which returns created=False → caller discards. One-winner guarantee.

    NOT used for Mode A delta events: Mode A consumers rely on serial dispatch
    order, not on fence checks.
    """

    @staticmethod
    def advance(
        tenant_id: uuid.UUID,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        consumer: str,
        event_seq: int,
    ) -> bool:
        """
        Returns True  — fence advanced; event should be processed.
        Returns False — event is stale (seq <= last_seen_seq); discard.
        Never raises.
        """
        try:
            now = tz.now()
            updated = AggregateSequenceFence.objects.filter(
                tenant_id=tenant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                consumer=consumer,
                last_seen_seq__lt=event_seq,
            ).update(last_seen_seq=event_seq, updated_at=now)

            if updated:
                return True

            # No existing row satisfying last_seen_seq < event_seq.
            # Two cases: row absent (first event) or event is stale.
            _, created = AggregateSequenceFence.objects.get_or_create(
                tenant_id=tenant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                consumer=consumer,
                defaults={"last_seen_seq": event_seq, "updated_at": now},
            )
            if not created:
                logger.debug(
                    "aggregate_fence_stale_event_discarded",
                    tenant_id=str(tenant_id),
                    aggregate_type=aggregate_type,
                    aggregate_id=str(aggregate_id),
                    consumer=consumer,
                    event_seq=event_seq,
                )
            return created

        except Exception as exc:
            logger.error(
                "aggregate_fence_advance_failed",
                tenant_id=str(tenant_id),
                aggregate_type=aggregate_type,
                aggregate_id=str(aggregate_id),
                consumer=consumer,
                event_seq=event_seq,
                error=str(exc),
            )
            return False
