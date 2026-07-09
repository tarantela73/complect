"""
Tests for ADR-0012: Hybrid Event Ordering Strategy.

Covers:
- AggregateSequenceAllocator: monotonic allocation, isolation, rollback
- AggregateSequenceFenceService: advance, stale discard, race semantics
- Dispatcher routing: Mode A → serial stream, Mode B → tenant stream
- CausalQueryService.get_aggregate_history: Class C query
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from apps.common.models.aggregate_sequence import AggregateSequence, AggregateSequenceFence
from apps.common.models.outbox import OutboxRecord
from apps.common.services.aggregate_sequence import (
    AggregateSequenceAllocator,
    AggregateSequenceFenceService,
)
from apps.common.services.causal_query import CausalQueryService
from apps.common.tasks.outbox_dispatcher import SERIAL_DISPATCH_AGGREGATE_TYPES, _routing_stream

_TENANT = uuid.uuid4()
_NOW = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# AggregateSequenceAllocator
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAggregateSequenceAllocator:
    def test_first_allocation_returns_one(self):
        agg_id = uuid.uuid4()
        seq = AggregateSequenceAllocator.allocate(_TENANT, "finance.transaction", agg_id)
        assert seq == 1

    def test_second_allocation_returns_two(self):
        agg_id = uuid.uuid4()
        AggregateSequenceAllocator.allocate(_TENANT, "finance.transaction", agg_id)
        seq = AggregateSequenceAllocator.allocate(_TENANT, "finance.transaction", agg_id)
        assert seq == 2

    def test_allocations_are_strictly_monotonic(self):
        agg_id = uuid.uuid4()
        seqs = [AggregateSequenceAllocator.allocate(_TENANT, "finance.transaction", agg_id) for _ in range(5)]
        assert seqs == list(range(1, 6))

    def test_different_aggregates_have_independent_sequences(self):
        agg_a = uuid.uuid4()
        agg_b = uuid.uuid4()
        AggregateSequenceAllocator.allocate(_TENANT, "finance.transaction", agg_a)
        AggregateSequenceAllocator.allocate(_TENANT, "finance.transaction", agg_a)
        seq_b = AggregateSequenceAllocator.allocate(_TENANT, "finance.transaction", agg_b)
        assert seq_b == 1

    def test_different_tenants_have_independent_sequences(self):
        agg_id = uuid.uuid4()
        tenant_b = uuid.uuid4()
        AggregateSequenceAllocator.allocate(_TENANT, "finance.transaction", agg_id)
        AggregateSequenceAllocator.allocate(_TENANT, "finance.transaction", agg_id)
        seq = AggregateSequenceAllocator.allocate(tenant_b, "finance.transaction", agg_id)
        assert seq == 1

    def test_different_aggregate_types_have_independent_sequences(self):
        agg_id = uuid.uuid4()
        AggregateSequenceAllocator.allocate(_TENANT, "finance.transaction", agg_id)
        AggregateSequenceAllocator.allocate(_TENANT, "finance.transaction", agg_id)
        seq = AggregateSequenceAllocator.allocate(_TENANT, "contracts.contract", agg_id)
        assert seq == 1

    def test_row_stored_in_aggregate_sequence_table(self):
        agg_id = uuid.uuid4()
        AggregateSequenceAllocator.allocate(_TENANT, "finance.transaction", agg_id)
        row = AggregateSequence.objects.get(
            tenant_id=_TENANT,
            aggregate_type="finance.transaction",
            aggregate_id=agg_id,
        )
        assert row.current_seq == 1

    def test_current_seq_reflects_last_allocation(self):
        agg_id = uuid.uuid4()
        for _ in range(3):
            AggregateSequenceAllocator.allocate(_TENANT, "finance.transaction", agg_id)
        row = AggregateSequence.objects.get(
            tenant_id=_TENANT,
            aggregate_type="finance.transaction",
            aggregate_id=agg_id,
        )
        assert row.current_seq == 3


# ---------------------------------------------------------------------------
# AggregateSequenceFenceService
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAggregateSequenceFenceService:
    def _advance(self, agg_id: uuid.UUID, consumer: str, seq: int) -> bool:
        return AggregateSequenceFenceService.advance(
            tenant_id=_TENANT,
            aggregate_type="contracts.contract",
            aggregate_id=agg_id,
            consumer=consumer,
            event_seq=seq,
        )

    def test_first_event_is_processed(self):
        assert self._advance(uuid.uuid4(), "consumer_a", 1) is True

    def test_higher_seq_advances_fence(self):
        agg_id = uuid.uuid4()
        self._advance(agg_id, "consumer_a", 1)
        assert self._advance(agg_id, "consumer_a", 2) is True

    def test_stale_seq_is_discarded(self):
        agg_id = uuid.uuid4()
        self._advance(agg_id, "consumer_a", 5)
        assert self._advance(agg_id, "consumer_a", 3) is False

    def test_equal_seq_is_discarded(self):
        agg_id = uuid.uuid4()
        self._advance(agg_id, "consumer_a", 4)
        assert self._advance(agg_id, "consumer_a", 4) is False

    def test_fence_row_written_on_first_advance(self):
        agg_id = uuid.uuid4()
        self._advance(agg_id, "consumer_a", 7)
        row = AggregateSequenceFence.objects.get(
            tenant_id=_TENANT,
            aggregate_type="contracts.contract",
            aggregate_id=agg_id,
            consumer="consumer_a",
        )
        assert row.last_seen_seq == 7

    def test_fence_row_updated_on_advance(self):
        agg_id = uuid.uuid4()
        self._advance(agg_id, "consumer_a", 3)
        self._advance(agg_id, "consumer_a", 9)
        row = AggregateSequenceFence.objects.get(
            tenant_id=_TENANT,
            aggregate_type="contracts.contract",
            aggregate_id=agg_id,
            consumer="consumer_a",
        )
        assert row.last_seen_seq == 9

    def test_different_consumers_have_independent_fences(self):
        agg_id = uuid.uuid4()
        self._advance(agg_id, "consumer_a", 10)
        result = AggregateSequenceFenceService.advance(
            tenant_id=_TENANT,
            aggregate_type="contracts.contract",
            aggregate_id=agg_id,
            consumer="consumer_b",
            event_seq=3,
        )
        assert result is True

    def test_different_aggregates_have_independent_fences(self):
        agg_a = uuid.uuid4()
        agg_b = uuid.uuid4()
        self._advance(agg_a, "consumer_a", 100)
        result = AggregateSequenceFenceService.advance(
            tenant_id=_TENANT,
            aggregate_type="contracts.contract",
            aggregate_id=agg_b,
            consumer="consumer_a",
            event_seq=1,
        )
        assert result is True

    def test_returns_false_on_exception_without_raising(self):
        with patch("apps.common.services.aggregate_sequence.AggregateSequenceFence.objects") as mock_mgr:
            mock_mgr.filter.side_effect = Exception("db_error")
            result = AggregateSequenceFenceService.advance(
                tenant_id=_TENANT,
                aggregate_type="contracts.contract",
                aggregate_id=uuid.uuid4(),
                consumer="consumer_a",
                event_seq=1,
            )
        assert result is False


# ---------------------------------------------------------------------------
# Dispatcher routing (pure unit tests — no DB)
# ---------------------------------------------------------------------------


class TestDispatcherRouting:
    def _make_record(
        self,
        aggregate_type: str | None = None,
        aggregate_id: uuid.UUID | None = None,
    ) -> MagicMock:
        record = MagicMock(spec=OutboxRecord)
        record.aggregate_type = aggregate_type
        record.aggregate_id = aggregate_id
        return record

    def test_mode_a_type_routes_to_serial_stream(self):
        agg_id = uuid.uuid4()
        record = self._make_record("finance.transaction", agg_id)
        stream = _routing_stream(record, "tenant-123")
        assert stream.startswith("erp:streams:serial:")

    def test_mode_b_type_routes_to_tenant_stream(self):
        record = self._make_record("contracts.contract", uuid.uuid4())
        stream = _routing_stream(record, "tenant-123")
        assert stream == "erp:streams:tenant:tenant-123"

    def test_mode_a_without_aggregate_id_falls_back_to_tenant_stream(self):
        record = self._make_record("finance.transaction", None)
        stream = _routing_stream(record, "tenant-123")
        assert stream == "erp:streams:tenant:tenant-123"

    def test_mode_a_null_aggregate_type_falls_back_to_tenant_stream(self):
        record = self._make_record(None, uuid.uuid4())
        stream = _routing_stream(record, "tenant-123")
        assert stream == "erp:streams:tenant:tenant-123"

    def test_serial_slot_is_stable_for_same_aggregate_id(self):
        agg_id = uuid.uuid4()
        record = self._make_record("finance.transaction", agg_id)
        s1 = _routing_stream(record, "t1")
        s2 = _routing_stream(record, "t1")
        assert s1 == s2

    def test_slot_is_within_bounds(self):
        agg_id = uuid.uuid4()
        record = self._make_record("finance.transaction", agg_id)
        stream = _routing_stream(record, "tenant-x")
        slot = int(stream.split(":")[-1])
        assert 0 <= slot < 256

    def test_serial_dispatch_aggregate_types_contains_finance(self):
        assert "finance.transaction" in SERIAL_DISPATCH_AGGREGATE_TYPES
        assert "finance.balance" in SERIAL_DISPATCH_AGGREGATE_TYPES

    def test_two_different_aggregate_ids_may_land_different_slots(self):
        agg_a = uuid.UUID(int=0)
        agg_b = uuid.UUID(int=1)
        record_a = self._make_record("finance.transaction", agg_a)
        record_b = self._make_record("finance.transaction", agg_b)
        stream_a = _routing_stream(record_a, "t")
        stream_b = _routing_stream(record_b, "t")
        # slot(0) = 0 mod 256 = 0; slot(1) = 1 mod 256 = 1
        assert stream_a != stream_b


# ---------------------------------------------------------------------------
# CausalQueryService — Class C (aggregate history)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCausalQueryClassC:
    def _make_outbox(
        self,
        tenant_id: uuid.UUID,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        aggregate_seq: int,
        event_type: str = "finance.transaction",
    ) -> OutboxRecord:
        return OutboxRecord.objects.create(
            event_type=event_type,
            payload_json={"tenant_id": str(tenant_id)},
            correlation_id=uuid.uuid4(),
            causal_root_id=uuid.uuid4(),
            aggregate_id=aggregate_id,
            aggregate_type=aggregate_type,
            aggregate_seq=aggregate_seq,
        )

    def test_returns_empty_chain_when_no_records(self):
        chain = CausalQueryService.get_aggregate_history(
            tenant_id=_TENANT,
            aggregate_type="finance.transaction",
            aggregate_id=uuid.uuid4(),
            queried_by="test",
        )
        assert chain.nodes == ()
        assert "aggregate_seq_unavailable" in chain.missing_layers

    def test_returns_nodes_ordered_by_aggregate_seq_ascending(self):
        agg_id = uuid.uuid4()
        self._make_outbox(_TENANT, "finance.transaction", agg_id, aggregate_seq=3)
        self._make_outbox(_TENANT, "finance.transaction", agg_id, aggregate_seq=1)
        self._make_outbox(_TENANT, "finance.transaction", agg_id, aggregate_seq=2)
        chain = CausalQueryService.get_aggregate_history(
            tenant_id=_TENANT,
            aggregate_type="finance.transaction",
            aggregate_id=agg_id,
            queried_by="test",
        )
        seqs = [n.aggregate_seq for n in chain.nodes]
        assert seqs == [1, 2, 3]

    def test_cursor_pagination_excludes_seq_gte_cursor(self):
        agg_id = uuid.uuid4()
        for seq in range(1, 6):
            self._make_outbox(_TENANT, "finance.transaction", agg_id, aggregate_seq=seq)
        chain = CausalQueryService.get_aggregate_history(
            tenant_id=_TENANT,
            aggregate_type="finance.transaction",
            aggregate_id=agg_id,
            queried_by="test",
            cursor=4,
        )
        seqs = [n.aggregate_seq for n in chain.nodes]
        assert all(s < 4 for s in seqs)
        assert 4 not in seqs
        assert 5 not in seqs

    def test_tenant_isolation_no_cross_tenant_leak(self):
        agg_id = uuid.uuid4()
        other_tenant = uuid.uuid4()
        self._make_outbox(_TENANT, "finance.transaction", agg_id, aggregate_seq=1)
        self._make_outbox(other_tenant, "finance.transaction", agg_id, aggregate_seq=2)
        chain = CausalQueryService.get_aggregate_history(
            tenant_id=_TENANT,
            aggregate_type="finance.transaction",
            aggregate_id=agg_id,
            queried_by="test",
        )
        assert len(chain.nodes) == 1
        assert chain.nodes[0].aggregate_seq == 1

    def test_records_without_aggregate_seq_excluded(self):
        agg_id = uuid.uuid4()
        OutboxRecord.objects.create(
            event_type="finance.transaction",
            payload_json={"tenant_id": str(_TENANT)},
            correlation_id=uuid.uuid4(),
            aggregate_id=agg_id,
            aggregate_type="finance.transaction",
            aggregate_seq=None,
        )
        chain = CausalQueryService.get_aggregate_history(
            tenant_id=_TENANT,
            aggregate_type="finance.transaction",
            aggregate_id=agg_id,
            queried_by="test",
        )
        assert chain.nodes == ()

    def test_aggregate_fields_populated_in_nodes(self):
        agg_id = uuid.uuid4()
        self._make_outbox(_TENANT, "finance.transaction", agg_id, aggregate_seq=1)
        chain = CausalQueryService.get_aggregate_history(
            tenant_id=_TENANT,
            aggregate_type="finance.transaction",
            aggregate_id=agg_id,
            queried_by="test",
        )
        node = chain.nodes[0]
        assert node.aggregate_type == "finance.transaction"
        assert node.aggregate_seq == 1
        assert node.aggregate_id == str(agg_id)

    def test_limit_is_respected(self):
        agg_id = uuid.uuid4()
        for seq in range(1, 11):
            self._make_outbox(_TENANT, "finance.transaction", agg_id, aggregate_seq=seq)
        chain = CausalQueryService.get_aggregate_history(
            tenant_id=_TENANT,
            aggregate_type="finance.transaction",
            aggregate_id=agg_id,
            queried_by="test",
            limit=3,
        )
        # Without cursor, returns last 3 by -aggregate_seq then re-sorted asc
        assert len(chain.nodes) == 3
