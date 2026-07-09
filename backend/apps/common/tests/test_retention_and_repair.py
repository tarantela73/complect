"""
Tests for ADR-0013: Outbox Retention Policy & Dead-Letter Repair Semantics.

Covers:
- archive_processed_outbox_records_task: lifecycle class filtering, archive+delete atomicity
- ConsumerRepairService: safety contract, ProcessedEvent reset, fence bypass
- TYPE VI ordering violation detection in CausalIncidentClassifier
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from django.utils import timezone as tz

from apps.common.models.aggregate_sequence import AggregateSequenceFence
from apps.common.models.outbox import OutboxRecord
from apps.common.models.outbox_archive import OutboxArchive
from apps.common.models.processed_event import ProcessedEvent
from apps.common.services.causal_incident import CausalIncidentClassifier
from apps.common.services.causal_query import CausalChain, CausalNode
from apps.common.services.consumer_repair import ConsumerRepairService
from apps.common.tasks.retention_manager import archive_processed_outbox_records_task

_TENANT = uuid.uuid4()
_NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _outbox(
    *,
    is_processed: bool = True,
    failed_permanently_at=None,
    processed_at=None,
    days_old: int = 35,
) -> OutboxRecord:
    record = OutboxRecord(
        event_type="finance.transaction",
        payload_json={"tenant_id": str(_TENANT)},
        correlation_id=uuid.uuid4(),
        is_processed=is_processed,
        failed_permanently_at=failed_permanently_at,
    )
    record.save()
    # Set created_at and processed_at after save to bypass auto_now_add.
    cutoff_past = tz.now() - timedelta(days=days_old)
    OutboxRecord.objects.filter(id=record.id).update(
        created_at=cutoff_past,
        processed_at=processed_at if processed_at is not None else (cutoff_past if is_processed else None),
    )
    return OutboxRecord.objects.get(id=record.id)


def _causal_node(
    aggregate_type: str | None = "finance.transaction",
    aggregate_id: str | None = None,
    aggregate_seq: int | None = None,
    event_type: str = "finance.transaction",
) -> CausalNode:
    return CausalNode(
        outbox_record_id=uuid.uuid4(),
        causal_root_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        tenant_id=_TENANT,
        occurred_at=_NOW,
        event_type=event_type,
        aggregate_id=aggregate_id or str(uuid.uuid4()),
        aggregate_type=aggregate_type,
        aggregate_seq=aggregate_seq,
        processing_status="acked",
        outbox_archived=False,
    )


def _chain(*nodes: CausalNode) -> CausalChain:
    root_id = uuid.uuid4()
    return CausalChain(
        root_id=root_id,
        nodes=tuple(nodes),
        chain_complete=True,
        missing_layers=(),
        queried_at=_NOW,
        queried_by="test",
    )


# ---------------------------------------------------------------------------
# TestArchiveTask
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestArchiveTask:
    def test_normal_records_moved_to_archive(self):
        record = _outbox(is_processed=True, failed_permanently_at=None, days_old=35)
        archive_processed_outbox_records_task()
        assert not OutboxRecord.objects.filter(id=record.id).exists()
        assert OutboxArchive.objects.filter(id=record.id).exists()

    def test_quarantine_records_not_archived(self):
        record = _outbox(
            is_processed=False,
            failed_permanently_at=tz.now() - timedelta(days=5),
            days_old=35,
        )
        archive_processed_outbox_records_task()
        assert OutboxRecord.objects.filter(id=record.id).exists()
        assert not OutboxArchive.objects.filter(id=record.id).exists()

    def test_pending_records_not_archived(self):
        record = _outbox(is_processed=False, failed_permanently_at=None, days_old=35)
        archive_processed_outbox_records_task()
        assert OutboxRecord.objects.filter(id=record.id).exists()
        assert not OutboxArchive.objects.filter(id=record.id).exists()

    def test_records_within_window_not_archived(self):
        record = _outbox(is_processed=True, failed_permanently_at=None, days_old=5)
        archive_processed_outbox_records_task()
        assert OutboxRecord.objects.filter(id=record.id).exists()
        assert not OutboxArchive.objects.filter(id=record.id).exists()

    def test_archive_preserves_all_fields(self):
        corr_id = uuid.uuid4()
        record = OutboxRecord(
            event_type="contracts.contract_created",
            payload_json={"tenant_id": str(_TENANT), "x": 1},
            correlation_id=corr_id,
            is_processed=True,
            retry_count=2,
        )
        record.save()
        cutoff_past = tz.now() - timedelta(days=35)
        OutboxRecord.objects.filter(id=record.id).update(
            created_at=cutoff_past,
            processed_at=cutoff_past,
        )

        archive_processed_outbox_records_task()

        archived = OutboxArchive.objects.get(id=record.id)
        assert archived.event_type == "contracts.contract_created"
        assert archived.correlation_id == corr_id
        assert archived.retry_count == 2
        assert archived.archived_at is not None

    def test_multiple_records_all_archived_in_batch(self):
        ids = []
        for _ in range(5):
            ids.append(_outbox(is_processed=True, days_old=35).id)

        archive_processed_outbox_records_task()

        for record_id in ids:
            assert not OutboxRecord.objects.filter(id=record_id).exists()
            assert OutboxArchive.objects.filter(id=record_id).exists()

    def test_idempotent_on_double_run(self):
        record = _outbox(is_processed=True, days_old=35)
        archive_processed_outbox_records_task()
        archive_processed_outbox_records_task()  # second run: nothing to do
        assert OutboxArchive.objects.filter(id=record.id).count() == 1


# ---------------------------------------------------------------------------
# TestConsumerRepairService
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConsumerRepairService:
    def _quarantine_record(self) -> OutboxRecord:
        record = OutboxRecord(
            event_type="finance.transaction",
            payload_json={"tenant_id": str(_TENANT)},
            correlation_id=uuid.uuid4(),
            is_processed=False,
            failed_permanently_at=tz.now(),
        )
        record.save()
        return record

    def test_reset_quarantine_record_succeeds(self):
        record = self._quarantine_record()
        ProcessedEvent.objects.create(
            outbox_record_id=record.id,
            consumer="finance.consumer",
            tenant_id=_TENANT,
            acked_at=tz.now(),
        )
        ConsumerRepairService.reset_for_repair(
            outbox_record_id=record.id,
            consumer="finance.consumer",
            operator_id="ops-001",
            reason="business action not executed",
        )
        assert not ProcessedEvent.objects.filter(outbox_record_id=record.id, consumer="finance.consumer").exists()

    def test_reset_non_quarantine_raises(self):
        record = OutboxRecord(
            event_type="finance.transaction",
            payload_json={"tenant_id": str(_TENANT)},
            correlation_id=uuid.uuid4(),
            is_processed=True,
            failed_permanently_at=None,
        )
        record.save()
        with pytest.raises(ValueError, match="quarantine-class"):
            ConsumerRepairService.reset_for_repair(
                outbox_record_id=record.id,
                consumer="finance.consumer",
                operator_id="ops-001",
                reason="test",
            )

    def test_reset_missing_record_raises(self):
        with pytest.raises(ValueError, match="not found or archived"):
            ConsumerRepairService.reset_for_repair(
                outbox_record_id=uuid.uuid4(),
                consumer="finance.consumer",
                operator_id="ops-001",
                reason="test",
            )

    def test_only_target_consumer_processed_event_deleted(self):
        record = self._quarantine_record()
        ProcessedEvent.objects.create(
            outbox_record_id=record.id,
            consumer="consumer.a",
            tenant_id=_TENANT,
            acked_at=tz.now(),
        )
        ProcessedEvent.objects.create(
            outbox_record_id=record.id,
            consumer="consumer.b",
            tenant_id=_TENANT,
            acked_at=tz.now(),
        )
        ConsumerRepairService.reset_for_repair(
            outbox_record_id=record.id,
            consumer="consumer.a",
            operator_id="ops-001",
            reason="test",
        )
        assert not ProcessedEvent.objects.filter(outbox_record_id=record.id, consumer="consumer.a").exists()
        assert ProcessedEvent.objects.filter(outbox_record_id=record.id, consumer="consumer.b").exists()

    def test_fence_reset_when_requested(self):
        record = self._quarantine_record()
        agg_id = uuid.uuid4()
        AggregateSequenceFence.objects.create(
            tenant_id=_TENANT,
            aggregate_type="contracts.contract",
            aggregate_id=agg_id,
            consumer="consumer.a",
            last_seen_seq=10,
            updated_at=tz.now(),
        )
        ConsumerRepairService.reset_for_repair(
            outbox_record_id=record.id,
            consumer="consumer.a",
            operator_id="ops-001",
            reason="fence bypass test",
            reset_fence=True,
            tenant_id=_TENANT,
            aggregate_type="contracts.contract",
            aggregate_id=agg_id,
            target_seq=8,
        )
        fence = AggregateSequenceFence.objects.get(
            tenant_id=_TENANT,
            aggregate_type="contracts.contract",
            aggregate_id=agg_id,
            consumer="consumer.a",
        )
        assert fence.last_seen_seq == 7  # target_seq - 1

    def test_fence_not_reset_by_default(self):
        record = self._quarantine_record()
        agg_id = uuid.uuid4()
        AggregateSequenceFence.objects.create(
            tenant_id=_TENANT,
            aggregate_type="contracts.contract",
            aggregate_id=agg_id,
            consumer="consumer.a",
            last_seen_seq=10,
            updated_at=tz.now(),
        )
        ConsumerRepairService.reset_for_repair(
            outbox_record_id=record.id,
            consumer="consumer.a",
            operator_id="ops-001",
            reason="no fence reset",
        )
        fence = AggregateSequenceFence.objects.get(
            tenant_id=_TENANT,
            aggregate_type="contracts.contract",
            aggregate_id=agg_id,
            consumer="consumer.a",
        )
        assert fence.last_seen_seq == 10  # unchanged


# ---------------------------------------------------------------------------
# TestTypeVIOrdering (pure unit tests — no DB)
# ---------------------------------------------------------------------------


class TestTypeVIOrdering:
    def test_no_ordering_violation_for_single_node(self):
        agg_id = str(uuid.uuid4())
        node = _causal_node(aggregate_id=agg_id, aggregate_seq=5)
        incident = CausalIncidentClassifier.classify(_chain(node))
        assert incident is None

    def test_no_ordering_violation_for_contiguous_seqs(self):
        agg_id = str(uuid.uuid4())
        nodes = [_causal_node(aggregate_id=agg_id, aggregate_seq=s) for s in [1, 2, 3]]
        incident = CausalIncidentClassifier.classify(_chain(*nodes))
        assert incident is None

    def test_ordering_violation_detected_for_gap(self):
        agg_id = str(uuid.uuid4())
        nodes = [_causal_node(aggregate_id=agg_id, aggregate_seq=s) for s in [1, 2, 4]]
        incident = CausalIncidentClassifier.classify(_chain(*nodes))
        assert incident is not None
        assert incident.incident_type == "ORDERING_VIOLATION"

    def test_ordering_violation_severity_critical_for_finance(self):
        agg_id = str(uuid.uuid4())
        nodes = [
            _causal_node(
                aggregate_id=agg_id,
                aggregate_seq=s,
                event_type="finance.transaction",
                aggregate_type="finance.transaction",
            )
            for s in [1, 3]
        ]
        incident = CausalIncidentClassifier.classify(_chain(*nodes))
        assert incident is not None
        assert incident.incident_type == "ORDERING_VIOLATION"
        assert incident.severity == "critical"

    def test_no_ordering_violation_when_aggregate_seq_is_none(self):
        agg_id = str(uuid.uuid4())
        # Pre-ADR-0012 nodes have aggregate_seq=None — must not trigger TYPE VI.
        nodes = [_causal_node(aggregate_id=agg_id, aggregate_seq=None) for _ in range(3)]
        incident = CausalIncidentClassifier.classify(_chain(*nodes))
        assert incident is None

    def test_different_aggregates_independent(self):
        agg_a = str(uuid.uuid4())
        agg_b = str(uuid.uuid4())
        # agg_a: contiguous [1,2,3]; agg_b: contiguous [1,2] — no violation.
        nodes = [_causal_node(aggregate_id=agg_a, aggregate_seq=s) for s in [1, 2, 3]] + [
            _causal_node(aggregate_id=agg_b, aggregate_seq=s) for s in [1, 2]
        ]
        incident = CausalIncidentClassifier.classify(_chain(*nodes))
        assert incident is None

    def test_ordering_violation_for_one_aggregate_in_multi_aggregate_chain(self):
        agg_a = str(uuid.uuid4())
        agg_b = str(uuid.uuid4())
        nodes = (
            [_causal_node(aggregate_id=agg_a, aggregate_seq=s) for s in [1, 3]]  # gap
            + [_causal_node(aggregate_id=agg_b, aggregate_seq=s) for s in [1, 2]]  # ok
        )
        incident = CausalIncidentClassifier.classify(_chain(*nodes))
        assert incident is not None
        assert incident.incident_type == "ORDERING_VIOLATION"

    def test_type_iii_takes_priority_over_type_vi(self):
        agg_id = str(uuid.uuid4())
        nodes = [_causal_node(aggregate_id=agg_id, aggregate_seq=s) for s in [1, 3]]
        incident = CausalIncidentClassifier.classify(
            _chain(*nodes),
            duplicate_ack_count=3,
        )
        assert incident is not None
        assert incident.incident_type == "EXECUTION_DUPLICATION"
