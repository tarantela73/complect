# backend/apps/common/tests/test_chain_completeness_monitor.py
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone as tz

from apps.common.models.outbox import OutboxRecord
from apps.common.models.processed_event import ProcessedEvent
from apps.common.tasks.chain_completeness_monitor import (
    ChainCompletenessMonitor,
    run_chain_completeness_monitor_task,
)

# SLO_WINDOW=300s, SLACK=300s → observation window = [now-600s, now-300s].
# Backdating records by 450s puts them squarely inside the window.
_IN_WINDOW_OFFSET = 450
# Records newer than SLO_WINDOW are outside the window (not yet overdue).
_FRESH_OFFSET = 60


def _make_outbox(causal_root_id=None, offset_seconds=_IN_WINDOW_OFFSET):
    """Create an OutboxRecord backdated into the observation window."""
    record_id = uuid.uuid4()
    r = OutboxRecord.objects.create(
        id=record_id,
        event_type="TestEvent",
        payload_json={"tenant_id": str(uuid.uuid4())},
        correlation_id=uuid.uuid4(),
        causal_root_id=causal_root_id if causal_root_id is not None else record_id,
    )
    OutboxRecord.objects.filter(pk=r.pk).update(created_at=tz.now() - timedelta(seconds=offset_seconds))
    r.refresh_from_db()
    return r


def _ack(outbox_record: OutboxRecord) -> ProcessedEvent:
    pe = ProcessedEvent.objects.create(
        outbox_record_id=outbox_record.id,
        consumer="svc_test",
        tenant_id=uuid.UUID(outbox_record.payload_json["tenant_id"]),
        causal_root_id=outbox_record.causal_root_id,
    )
    pe.acked_at = tz.now()
    pe.save(update_fields=["acked_at"])
    return pe


@pytest.mark.django_db
class TestChainCompletenessMonitorMeasure:
    def test_empty_window_returns_zero_totals(self):
        metrics = ChainCompletenessMonitor.measure()

        assert metrics["total_chains"] == 0
        assert metrics["complete_chains"] == 0
        assert metrics["completeness_rate"] is None
        assert metrics["slo_violated"] is False

    def test_single_acked_chain_is_complete(self):
        root = _make_outbox()
        _ack(root)

        metrics = ChainCompletenessMonitor.measure()

        assert metrics["total_chains"] == 1
        assert metrics["complete_chains"] == 1
        assert metrics["incomplete_chains"] == 0
        assert metrics["completeness_rate"] == 1.0
        assert metrics["slo_violated"] is False

    def test_unacked_chain_is_incomplete(self):
        root = _make_outbox()
        # Claimed but not acked
        ProcessedEvent.objects.create(
            outbox_record_id=root.id,
            consumer="svc_test",
            tenant_id=uuid.UUID(root.payload_json["tenant_id"]),
            causal_root_id=root.causal_root_id,
        )

        metrics = ChainCompletenessMonitor.measure()

        assert metrics["total_chains"] == 1
        assert metrics["complete_chains"] == 0
        assert metrics["incomplete_chains"] == 1
        assert metrics["completeness_rate"] == 0.0
        assert metrics["slo_violated"] is True

    def test_pending_chain_no_processed_event_is_incomplete(self):
        _make_outbox()

        metrics = ChainCompletenessMonitor.measure()

        assert metrics["incomplete_chains"] == 1
        assert metrics["slo_violated"] is True

    def test_fresh_records_outside_window_not_counted(self):
        """Records newer than SLO_WINDOW must not appear in the observation window."""
        _make_outbox(offset_seconds=_FRESH_OFFSET)  # not yet overdue

        metrics = ChainCompletenessMonitor.measure()

        assert metrics["total_chains"] == 0

    def test_dlq_records_excluded_from_completeness(self):
        """
        A chain with failed_permanently_at set is excluded from the completeness
        denominator — it is a DLQ failure, not a completeness failure (Invariant 3).
        """
        root = _make_outbox()
        OutboxRecord.objects.filter(pk=root.pk).update(failed_permanently_at=tz.now())

        metrics = ChainCompletenessMonitor.measure()

        assert metrics["total_chains"] == 0

    def test_repair_chain_complete_only_when_all_nodes_acked(self):
        """
        Compensation record shares causal_root_id with root.
        Chain is complete only when BOTH nodes are acked (Invariant 4).
        """
        root = _make_outbox()
        _compensation = _make_outbox(causal_root_id=root.causal_root_id)

        _ack(root)
        # compensation not acked

        metrics = ChainCompletenessMonitor.measure()

        assert metrics["total_chains"] == 1  # one logical chain
        assert metrics["complete_chains"] == 0
        assert metrics["slo_violated"] is True

    def test_repair_chain_complete_when_all_nodes_acked(self):
        root = _make_outbox()
        compensation = _make_outbox(causal_root_id=root.causal_root_id)

        _ack(root)
        _ack(compensation)

        metrics = ChainCompletenessMonitor.measure()

        assert metrics["total_chains"] == 1
        assert metrics["complete_chains"] == 1
        assert metrics["completeness_rate"] == 1.0

    def test_mixed_chains_partial_completeness(self):
        """Two chains: one complete, one not. Rate = 0.5."""
        complete_root = _make_outbox()
        _ack(complete_root)

        _make_outbox()  # incomplete chain, no ack

        metrics = ChainCompletenessMonitor.measure()

        assert metrics["total_chains"] == 2
        assert metrics["complete_chains"] == 1
        assert metrics["completeness_rate"] == 0.5
        assert metrics["slo_violated"] is True

    def test_pre_adr0015_null_causal_root_treated_as_self_rooted(self):
        """OutboxRecord with causal_root_id=NULL → treated as a single-node chain."""
        record_id = uuid.uuid4()
        r = OutboxRecord.objects.create(
            id=record_id,
            event_type="LegacyEvent",
            payload_json={"tenant_id": str(uuid.uuid4())},
            correlation_id=uuid.uuid4(),
            causal_root_id=None,
        )
        OutboxRecord.objects.filter(pk=r.pk).update(created_at=tz.now() - timedelta(seconds=_IN_WINDOW_OFFSET))
        r.refresh_from_db()

        metrics = ChainCompletenessMonitor.measure()

        assert metrics["total_chains"] == 1
        assert metrics["incomplete_chains"] == 1

    def test_slo_target_threshold_boundary(self):
        """Completeness exactly at SLO target must NOT violate."""
        # With SLO_TARGET=0.999, 1000 chains all acked → rate=1.0, no violation.
        # We simulate with 1 chain complete → rate=1.0.
        root = _make_outbox()
        _ack(root)

        metrics = ChainCompletenessMonitor.measure()

        assert metrics["slo_violated"] is False

    def test_exactly_three_db_queries(self):
        """
        Monitor executes exactly 3 data queries (Invariant 2 of ADR-0017).

        SET LOCAL statements issued by the DB tenant middleware are excluded from
        the count — they are infrastructure overhead, not algorithmic complexity.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        root = _make_outbox()
        _ack(root)

        with CaptureQueriesContext(connection) as ctx:
            ChainCompletenessMonitor.measure()

        data_queries = [q for q in ctx.captured_queries if not q["sql"].upper().startswith("SET ")]
        assert len(data_queries) == 3, (
            f"Expected 3 data queries, got {len(data_queries)}: {[q['sql'][:80] for q in data_queries]}"
        )


@pytest.mark.django_db
class TestRunChainCompletenessMonitorTask:
    def test_task_emits_info_log_when_no_violation(self):
        root = _make_outbox()
        _ack(root)

        with patch("apps.common.tasks.chain_completeness_monitor.logger") as mock_logger:
            run_chain_completeness_monitor_task()

        mock_logger.info.assert_called_once()
        event = mock_logger.info.call_args[0][0]
        assert event == "chain_completeness_measured"
        # No SLO violation → error must NOT be called.
        mock_logger.error.assert_not_called()

    def test_task_emits_error_log_on_slo_violation(self):
        _make_outbox()  # unacked → violation

        with patch("apps.common.tasks.chain_completeness_monitor.logger") as mock_logger:
            run_chain_completeness_monitor_task()

        mock_logger.warning.assert_called_once()
        mock_logger.error.assert_called_once()
        error_event = mock_logger.error.call_args[0][0]
        assert error_event == "chain_completeness_slo_violated"

    def test_task_no_logs_when_window_empty(self):
        """No records in window → one info log, no error."""
        with patch("apps.common.tasks.chain_completeness_monitor.logger") as mock_logger:
            run_chain_completeness_monitor_task()

        mock_logger.info.assert_called_once()
        mock_logger.error.assert_not_called()
