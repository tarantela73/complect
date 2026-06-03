import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone as tz

from apps.common.models.outbox import OutboxRecord
from apps.common.models.processed_event import ProcessedEvent
from apps.common.services.causal_query import CausalQueryService


def _make_outbox(causal_root_id=None, correlation_id=None, tenant_id=None, failed=False):
    record_id = uuid.uuid4()
    t_id = tenant_id or uuid.uuid4()
    r = OutboxRecord.objects.create(
        id=record_id,
        event_type="ContractClosedEvent",
        payload_json={"tenant_id": str(t_id)},
        correlation_id=correlation_id or uuid.uuid4(),
        causal_root_id=causal_root_id if causal_root_id is not None else record_id,
        failed_permanently_at=tz.now() if failed else None,
    )
    return r


def _ack_outbox(outbox_record: OutboxRecord, tenant_id=None):
    t_id = tenant_id or uuid.UUID(outbox_record.payload_json["tenant_id"])
    pe = ProcessedEvent.objects.create(
        outbox_record_id=outbox_record.id,
        consumer="svc_test",
        tenant_id=t_id,
        causal_root_id=outbox_record.causal_root_id,
    )
    pe.acked_at = tz.now()
    pe.save(update_fields=["acked_at"])
    return pe


@pytest.mark.django_db
class TestGetChainByRoot:

    def test_single_node_acked_chain_is_complete(self):
        """Normal flow: root record created, acked → chain_complete=True."""
        root = _make_outbox()
        _ack_outbox(root)

        chain = CausalQueryService.get_chain_by_root(
            causal_root_id=root.causal_root_id,
            tenant_id=uuid.UUID(root.payload_json["tenant_id"]),
            queried_by="test",
        )

        assert chain.chain_complete is True
        assert len(chain.nodes) == 1
        assert chain.nodes[0].outbox_record_id == root.id
        assert chain.nodes[0].processing_status == "acked"
        assert chain.nodes[0].outbox_archived is False

    def test_unacked_chain_is_incomplete(self):
        """Claimed but not acked → chain_complete=False."""
        root = _make_outbox()
        tenant_id = uuid.UUID(root.payload_json["tenant_id"])
        ProcessedEvent.objects.create(
            outbox_record_id=root.id,
            consumer="svc_test",
            tenant_id=tenant_id,
            causal_root_id=root.causal_root_id,
            # acked_at intentionally NULL
        )

        chain = CausalQueryService.get_chain_by_root(
            causal_root_id=root.causal_root_id,
            tenant_id=tenant_id,
            queried_by="test",
        )

        assert chain.chain_complete is False
        assert chain.nodes[0].processing_status == "claimed"

    def test_pending_node_no_processed_event(self):
        """Dispatched but no consumer claim yet → status=pending."""
        root = _make_outbox()
        chain = CausalQueryService.get_chain_by_root(
            causal_root_id=root.causal_root_id,
            tenant_id=uuid.UUID(root.payload_json["tenant_id"]),
            queried_by="test",
        )

        assert chain.chain_complete is False
        assert chain.nodes[0].processing_status == "pending"

    def test_permanently_failed_node_status_dlq(self):
        """failed_permanently_at set, no ProcessedEvent → status=dlq."""
        root = _make_outbox(failed=True)
        chain = CausalQueryService.get_chain_by_root(
            causal_root_id=root.causal_root_id,
            tenant_id=uuid.UUID(root.payload_json["tenant_id"]),
            queried_by="test",
        )

        assert chain.chain_complete is False
        assert chain.nodes[0].processing_status == "dlq"

    def test_repair_chain_includes_both_root_and_compensation(self):
        """
        Repair flow: root record + compensation record with same causal_root_id.
        Query must return both nodes.
        """
        root = _make_outbox()
        compensation = _make_outbox(causal_root_id=root.causal_root_id)

        chain = CausalQueryService.get_chain_by_root(
            causal_root_id=root.causal_root_id,
            tenant_id=uuid.UUID(root.payload_json["tenant_id"]),
            queried_by="test",
        )

        node_ids = {n.outbox_record_id for n in chain.nodes}
        assert root.id in node_ids
        assert compensation.id in node_ids
        assert len(chain.nodes) == 2

    def test_repair_chain_complete_only_when_all_acked(self):
        """chain_complete=True iff ALL nodes in the repair chain are acked."""
        root = _make_outbox()
        compensation = _make_outbox(causal_root_id=root.causal_root_id)

        _ack_outbox(root)
        # compensation not acked

        chain = CausalQueryService.get_chain_by_root(
            causal_root_id=root.causal_root_id,
            tenant_id=uuid.UUID(root.payload_json["tenant_id"]),
            queried_by="test",
        )

        assert chain.chain_complete is False

        _ack_outbox(compensation)

        chain2 = CausalQueryService.get_chain_by_root(
            causal_root_id=root.causal_root_id,
            tenant_id=uuid.UUID(root.payload_json["tenant_id"]),
            queried_by="test",
        )
        assert chain2.chain_complete is True

    def test_unknown_root_returns_empty_chain_with_missing_layer(self):
        """Non-existent causal_root_id returns empty nodes and 'outbox' missing layer."""
        chain = CausalQueryService.get_chain_by_root(
            causal_root_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            queried_by="test",
        )

        assert chain.chain_complete is False
        assert len(chain.nodes) == 0
        assert "outbox" in chain.missing_layers

    def test_post_archival_node_appears_via_processed_event(self):
        """
        When OutboxRecord is archived (deleted) but ProcessedEvent with causal_root_id
        still exists → node appears as outbox_archived=True, status=acked.
        """
        root = _make_outbox()
        root_id = root.causal_root_id
        archived_outbox_id = uuid.uuid4()
        tenant_id = uuid.UUID(root.payload_json["tenant_id"])

        # Simulate archived node: ProcessedEvent exists, OutboxRecord does not.
        ProcessedEvent.objects.create(
            outbox_record_id=archived_outbox_id,
            consumer="svc_test",
            tenant_id=tenant_id,
            causal_root_id=root_id,
            acked_at=tz.now(),
        )
        # The root OutboxRecord itself still exists (it wasn't archived).
        _ack_outbox(root)

        chain = CausalQueryService.get_chain_by_root(
            causal_root_id=root_id,
            tenant_id=tenant_id,
            queried_by="test",
        )

        # chain_complete=False: archived nodes detected
        assert chain.chain_complete is False
        archived_nodes = [n for n in chain.nodes if n.outbox_archived]
        assert len(archived_nodes) == 1
        assert archived_nodes[0].outbox_record_id == archived_outbox_id

    def test_audit_log_emitted(self):
        """Every get_chain_by_root() must emit a structlog causal_query_executed entry."""
        root = _make_outbox()

        with patch("apps.common.services.causal_query.logger") as mock_logger:
            CausalQueryService.get_chain_by_root(
                causal_root_id=root.causal_root_id,
                tenant_id=uuid.UUID(root.payload_json["tenant_id"]),
                queried_by="operator_42",
            )

        mock_logger.info.assert_called_once()
        call_kwargs = mock_logger.info.call_args
        assert call_kwargs[0][0] == "causal_query_executed"
        assert call_kwargs[1]["query_class"] == "chain_by_root"
        assert call_kwargs[1]["queried_by"] == "operator_42"


@pytest.mark.django_db
class TestGetChainByCorrelation:

    def test_returns_all_events_with_correlation_id(self):
        """Class B query returns all OutboxRecords sharing the same correlation_id."""
        corr_id = uuid.uuid4()
        r1 = _make_outbox(correlation_id=corr_id)
        r2 = _make_outbox(correlation_id=corr_id)
        # Different correlation — must not appear.
        _make_outbox()

        chain = CausalQueryService.get_chain_by_correlation(
            correlation_id=corr_id,
            tenant_id=uuid.UUID(r1.payload_json["tenant_id"]),
            queried_by="test",
        )

        node_ids = {n.outbox_record_id for n in chain.nodes}
        assert r1.id in node_ids
        assert r2.id in node_ids
        assert len(chain.nodes) == 2

    def test_chain_complete_when_all_acked(self):
        corr_id = uuid.uuid4()
        r1 = _make_outbox(correlation_id=corr_id)
        r2 = _make_outbox(correlation_id=corr_id)
        _ack_outbox(r1)
        _ack_outbox(r2)

        chain = CausalQueryService.get_chain_by_correlation(
            correlation_id=corr_id,
            tenant_id=uuid.UUID(r1.payload_json["tenant_id"]),
            queried_by="test",
        )

        assert chain.chain_complete is True

    def test_unknown_correlation_id_returns_empty(self):
        chain = CausalQueryService.get_chain_by_correlation(
            correlation_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            queried_by="test",
        )

        assert len(chain.nodes) == 0
        assert "outbox" in chain.missing_layers

    def test_nodes_ordered_by_occurred_at(self):
        """Class B nodes must be ordered by occurred_at ascending."""
        corr_id = uuid.uuid4()
        r1 = _make_outbox(correlation_id=corr_id)
        # Force r2 to have a later created_at
        OutboxRecord.objects.filter(pk=r1.pk).update(
            created_at=tz.now() - timedelta(seconds=10)
        )
        r2 = _make_outbox(correlation_id=corr_id)

        chain = CausalQueryService.get_chain_by_correlation(
            correlation_id=corr_id,
            tenant_id=uuid.UUID(r1.payload_json["tenant_id"]),
            queried_by="test",
        )

        occurred_ats = [n.occurred_at for n in chain.nodes]
        assert occurred_ats == sorted(occurred_ats)


@pytest.mark.django_db
class TestGetAggregateHistory:

    def test_returns_empty_chain_with_aggregate_seq_unavailable(self):
        """Class C is blocked on ADR-0012 — must return empty chain with correct missing layer."""
        chain = CausalQueryService.get_aggregate_history(
            tenant_id=uuid.uuid4(),
            aggregate_type="finance.transaction",
            aggregate_id=str(uuid.uuid4()),
            queried_by="test",
        )

        assert chain.chain_complete is False
        assert len(chain.nodes) == 0
        assert "aggregate_seq_unavailable" in chain.missing_layers
