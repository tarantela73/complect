"""
Domain Consumer Framework (BaseDomainConsumer).

Reference implementation layer over ADR-0011 (Lease/Reclaim), ADR-0012 (Ordering),
ADR-0019 (Incident Routing). NOT a separate ADR — architectural decisions are already
settled; this is the orchestration glue.

Two ordering modes:
  STRICT — Mode A. No fence check. Ordering guaranteed operationally by single-concurrency
            worker on the Finance Celery queue (ADR-0012 Slot Locking Invariant).
  FENCED  — Mode B. AggregateSequenceFenceService.advance() discards stale snapshots.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Literal

import structlog

from apps.common.services.aggregate_sequence import AggregateSequenceFenceService
from apps.common.services.consumer_idempotency import ConsumerIdempotencyService

if TYPE_CHECKING:
    from apps.common.models.outbox import OutboxRecord

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """Strongly-typed view over an OutboxRecord. Passed to BaseDomainConsumer.process()."""

    outbox_record_id: uuid.UUID
    causal_root_id: uuid.UUID | None
    correlation_id: uuid.UUID
    tenant_id: uuid.UUID
    aggregate_id: uuid.UUID | None
    aggregate_type: str | None
    aggregate_seq: int | None
    event_type: str
    payload: dict = field(default_factory=dict)


class BaseDomainConsumer(ABC):
    """
    Abstract base for all domain event consumers.

    Subclass contract:
        consumer_name        — unique identifier for ProcessedEvent tracking
        ordering_mode        — "STRICT" (Mode A, Finance) or "FENCED" (Mode B, snapshots)
        handled_event_types  — frozenset of EVENT_TYPE strings this consumer processes
        process(event)       — domain logic; called exactly once per non-duplicate event

    Built-ins (no boilerplate in subclasses):
        - Idempotency: claim → reclaim on expired lease → discard on duplicate
        - Fence check (FENCED only): discard stale snapshots via AggregateSequenceFenceService
        - Incident routing: route + log on any process() failure before re-raise
        - Causal propagation: causal_root_id, correlation_id, tenant_id extracted from record
    """

    consumer_name: ClassVar[str]
    ordering_mode: ClassVar[Literal["STRICT", "FENCED"]]
    handled_event_types: ClassVar[frozenset[str]]
    supports_repair: ClassVar[bool] = True

    def __init__(self) -> None:
        self._instance_id = uuid.uuid4()

    @abstractmethod
    def process(self, event: StreamEvent) -> None: ...

    @classmethod
    def _domain(cls) -> str:
        return cls.consumer_name.split(".")[0] if "." in cls.consumer_name else cls.consumer_name

    @staticmethod
    def _to_stream_event(record: "OutboxRecord") -> StreamEvent:
        payload = record.payload_json or {}
        tenant_raw = payload.get("tenant_id", "00000000-0000-0000-0000-000000000000")
        try:
            tenant_id = uuid.UUID(str(tenant_raw))
        except (ValueError, AttributeError):
            tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000000")
        return StreamEvent(
            outbox_record_id=record.id,
            causal_root_id=record.causal_root_id,
            correlation_id=record.correlation_id,
            tenant_id=tenant_id,
            aggregate_id=record.aggregate_id,
            aggregate_type=record.aggregate_type,
            aggregate_seq=record.aggregate_seq,
            event_type=record.event_type,
            payload=payload,
        )

    def handle(self, record: "OutboxRecord") -> None:
        """
        Main entry point. Called by the Celery task per OutboxRecord.

        Orchestrates: claim → fence (FENCED) → process → ack.
        On process() failure: route incident, then re-raise for Celery retry.
        """
        event = self._to_stream_event(record)

        claimed = ConsumerIdempotencyService.claim(
            outbox_record_id=event.outbox_record_id,
            consumer=self.consumer_name,
            tenant_id=event.tenant_id,
            instance_id=self._instance_id,
            causal_root_id=event.causal_root_id,
        )
        if not claimed:
            reclaimed = ConsumerIdempotencyService.reclaim(
                outbox_record_id=event.outbox_record_id,
                consumer=self.consumer_name,
                tenant_id=event.tenant_id,
                instance_id=self._instance_id,
            )
            if not reclaimed:
                logger.debug(
                    "consumer_event_duplicate_discarded",
                    consumer=self.consumer_name,
                    outbox_record_id=str(event.outbox_record_id),
                )
                return

        try:
            if (
                self.ordering_mode == "FENCED"
                and event.aggregate_id is not None
                and event.aggregate_seq is not None
            ):
                should_process = AggregateSequenceFenceService.advance(
                    tenant_id=event.tenant_id,
                    aggregate_type=event.aggregate_type or "",
                    aggregate_id=event.aggregate_id,
                    consumer=self.consumer_name,
                    event_seq=event.aggregate_seq,
                )
                if not should_process:
                    ConsumerIdempotencyService.ack(
                        outbox_record_id=event.outbox_record_id,
                        consumer=self.consumer_name,
                        instance_id=self._instance_id,
                    )
                    logger.debug(
                        "consumer_event_fence_discarded",
                        consumer=self.consumer_name,
                        outbox_record_id=str(event.outbox_record_id),
                        aggregate_seq=event.aggregate_seq,
                    )
                    return

            self.process(event)

            ConsumerIdempotencyService.ack(
                outbox_record_id=event.outbox_record_id,
                consumer=self.consumer_name,
                instance_id=self._instance_id,
            )

        except Exception as exc:
            self._route_incident(event, exc)
            raise

    def run_batch(self, batch_size: int = 100) -> int:
        """
        Poll DB for unprocessed records and handle each.

        Mode A (STRICT): ordered by (aggregate_id, aggregate_seq) — per-aggregate ordering
        within the batch. Worker-level single-concurrency (Finance queue) prevents races.

        Mode B (FENCED): ordered by created_at; fence check inside handle() discards stale.

        Returns count of records picked up (includes duplicates discarded before processing).
        """
        from django.db.models import Exists, F, OuterRef

        from apps.common.models.outbox import OutboxRecord
        from apps.common.models.processed_event import ProcessedEvent

        already_acked = ProcessedEvent.objects.filter(
            outbox_record_id=OuterRef("pk"),
            consumer=self.consumer_name,
            acked_at__isnull=False,
        )

        qs = (
            OutboxRecord.objects.filter(
                event_type__in=self.handled_event_types,
                is_processed=True,
                failed_permanently_at__isnull=True,
            )
            .exclude(Exists(already_acked))
        )

        if self.ordering_mode == "STRICT":
            qs = qs.order_by("aggregate_id", F("aggregate_seq").asc(nulls_last=True))
        else:
            qs = qs.order_by("created_at")

        records = list(qs[:batch_size])

        for record in records:
            self.handle(record)

        return len(records)

    def _route_incident(self, event: StreamEvent, exc: Exception) -> None:
        from apps.common.services.incident_routing import IncidentContext, IncidentRoutingPolicy

        try:
            ctx = IncidentContext(
                incidents=(),
                causal_root_id=event.causal_root_id,
                correlation_id=event.correlation_id,
                chain_slo_violated=True,
                affected_domains=frozenset({self._domain()}),
            )
            decision = IncidentRoutingPolicy.route(ctx)
            logger.error(
                "consumer_processing_failed",
                consumer=self.consumer_name,
                event_type=event.event_type,
                exc_type=type(exc).__name__,
                **decision.structlog_labels,
            )
        except Exception:
            pass
