"""
Domain Consumer Framework (BaseDomainConsumer).

Reference implementation layer over ADR-0011 (Lease/Reclaim), ADR-0012 (Ordering),
ADR-0019 (Incident Routing). NOT a separate ADR — architectural decisions are already
settled; this is the orchestration glue.

Two ordering modes:
  STRICT — Mode A. No fence check. Ordering guaranteed operationally by single-concurrency
            worker on the Finance Celery queue (ADR-0012 Slot Locking Invariant).
  FENCED  — Mode B. AggregateSequenceFenceService.advance() discards stale snapshots.

Emission enforcement:
  required_emissions — hard contract: CausalEmissionMissingError raised if absent after process().
  optional_emissions — soft contract: warning logged, no raise.
  Override _emission_required(event) to gate required checks on payload conditions
  (e.g. skip when remaining_amount == 0 to avoid false positives).

  Safety invariant: _verify_emission() queries OutboxRecord by correlation_id using an
  inline SELECT (not on_commit). This is correct because process() commits its inner
  @atomic before returning — all writes are visible on the same DB connection. If handle()
  is wrapped in an outer @atomic in the future, the writes remain visible within that
  transaction (same connection, same transaction scope). Deferred on_commit verification
  would break the ack-before-verify ordering: if verification fires post-commit, the event
  is already acked and a retry cannot reprocess it.
"""
from __future__ import annotations

import time
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


class CausalEmissionMissingError(Exception):
    """
    Raised by BaseDomainConsumer._verify_emission() when process() completed
    without writing an expected outbox event.

    Routed to IncidentRoutingPolicy before re-raise — same path as process() failures.
    """

    def __init__(
        self,
        *,
        consumer: str,
        outbox_record_id: uuid.UUID,
        correlation_id: uuid.UUID,
        missing_event_types: frozenset[str],
    ) -> None:
        self.consumer = consumer
        self.outbox_record_id = outbox_record_id
        self.correlation_id = correlation_id
        self.missing_event_types = missing_event_types
        super().__init__(
            f"Consumer {consumer!r} completed without emitting: {missing_event_types}"
        )


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
    # Hard contract: CausalEmissionMissingError if absent after process().
    # Gate with _emission_required() to avoid false positives on conditional branches.
    required_emissions: ClassVar[frozenset[str] | None] = None
    # Soft contract: warning logged only, no raise. No predicate needed.
    optional_emissions: ClassVar[frozenset[str]] = frozenset()

    def __init__(self) -> None:
        self._instance_id = uuid.uuid4()
        self._total_verify_ms: float = 0.0

    @abstractmethod
    def process(self, event: StreamEvent) -> None: ...

    def _emission_required(self, event: StreamEvent) -> bool:
        """
        Gates required_emissions enforcement. Does not affect optional_emissions.
        Override to suppress the hard check on conditional payload branches.
        """
        return self.required_emissions is not None

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

            if self.required_emissions is not None or self.optional_emissions:
                _t0 = time.perf_counter()
                self._verify_emission(event)
                self._total_verify_ms += (time.perf_counter() - _t0) * 1000

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

        Returns count of records picked up. Emits consumer_batch_complete log with
        execution decomposition: picked_up / acked / failed / verify_ms.
        On first handle() failure the batch is aborted (status="aborted") and re-raises
        so Celery retry can reprocess the failing record.
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
        picked_up = len(records)
        acked = 0
        failed = 0

        for record in records:
            try:
                self.handle(record)
                acked += 1
            except Exception:
                failed += 1
                logger.info(
                    "consumer_batch_complete",
                    consumer=self.consumer_name,
                    picked_up=picked_up,
                    acked=acked,
                    failed=failed,
                    verify_ms=round(self._total_verify_ms, 2),
                    status="aborted",
                )
                raise

        if picked_up:
            logger.info(
                "consumer_batch_complete",
                consumer=self.consumer_name,
                picked_up=picked_up,
                acked=acked,
                failed=failed,
                verify_ms=round(self._total_verify_ms, 2),
                status="complete",
            )

        return picked_up

    def _verify_emission(self, event: StreamEvent) -> None:
        from apps.common.models.outbox import OutboxRecord as _OutboxRecord

        if self.required_emissions is not None and self._emission_required(event):
            missing = frozenset(
                et
                for et in self.required_emissions
                if not _OutboxRecord.objects.filter(
                    correlation_id=event.correlation_id,
                    event_type=et,
                ).exists()
            )
            if missing:
                raise CausalEmissionMissingError(
                    consumer=self.consumer_name,
                    outbox_record_id=event.outbox_record_id,
                    correlation_id=event.correlation_id,
                    missing_event_types=missing,
                )

        for et in self.optional_emissions:
            if not _OutboxRecord.objects.filter(
                correlation_id=event.correlation_id,
                event_type=et,
            ).exists():
                logger.warning(
                    "consumer_optional_emission_absent",
                    consumer=self.consumer_name,
                    event_type=et,
                    outbox_record_id=str(event.outbox_record_id),
                    correlation_id=str(event.correlation_id),
                )

    def _route_incident(self, event: StreamEvent, exc: Exception) -> None:
        from apps.common.services.incident_routing import IncidentContext, IncidentRoutingPolicy

        try:
            # Dedup key prevents incident storm on Celery retry loops.
            # causal_root_id is stable across retries for the same causal chain.
            dedup_key = (
                f"incident:dedup:{self.consumer_name}"
                f":{event.causal_root_id or event.outbox_record_id}"
                f":{type(exc).__name__}"
            )
            from django.core.cache import cache

            if not cache.add(dedup_key, 1, timeout=300):
                logger.debug(
                    "consumer_incident_deduplicated",
                    consumer=self.consumer_name,
                    event_type=event.event_type,
                    exc_type=type(exc).__name__,
                    dedup_key=dedup_key,
                )
                return

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
