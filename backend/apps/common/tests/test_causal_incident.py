"""
Pure unit tests for CausalIncidentClassifier (ADR-0018).
No DB access — CausalChain is constructed directly.
"""

import uuid
from datetime import datetime, timezone

import pytest

from apps.common.services.causal_incident import (
    CausalIncidentClassifier,
)
from apps.common.services.causal_query import CausalChain, CausalNode

_NOW = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)
_ROOT = uuid.uuid4()
_CORR = uuid.uuid4()
_TENANT = uuid.uuid4()


def _node(
    *,
    outbox_record_id: uuid.UUID | None = None,
    causal_root_id: uuid.UUID | None = None,
    event_type: str = "contracts.created",
    processing_status: str = "acked",
    outbox_archived: bool = False,
) -> CausalNode:
    return CausalNode(
        outbox_record_id=outbox_record_id or uuid.uuid4(),
        causal_root_id=causal_root_id or _ROOT,
        correlation_id=_CORR,
        tenant_id=_TENANT,
        occurred_at=_NOW,
        event_type=event_type,
        aggregate_id=None,
        aggregate_type=None,
        aggregate_seq=None,
        processing_status=processing_status,
        outbox_archived=outbox_archived,
    )


def _chain(
    nodes: tuple[CausalNode, ...],
    chain_complete: bool = True,
    missing_layers: tuple[str, ...] = (),
    root_id: uuid.UUID | None = None,
) -> CausalChain:
    return CausalChain(
        root_id=root_id or _ROOT,
        nodes=nodes,
        chain_complete=chain_complete,
        missing_layers=missing_layers,
        queried_at=_NOW,
        queried_by="test",
    )


class _Registry:
    """Minimal DomainEffectRegistry stub."""

    def __init__(
        self,
        expected: frozenset[str],
        observed: frozenset[str],
    ) -> None:
        self._expected = expected
        self._observed = observed

    def expected_effects(self, event_type: str) -> frozenset[str]:
        return self._expected

    def observed_effects(self, causal_root_id: uuid.UUID) -> frozenset[str]:
        return self._observed


class TestEmptyChain:
    def test_empty_nodes_returns_none(self):
        assert CausalIncidentClassifier.classify(_chain(nodes=())) is None


class TestTypeIII:
    def test_duplicate_ack_count_gt_1_returns_execution_duplication(self):
        chain = _chain(nodes=(_node(),), chain_complete=True)
        result = CausalIncidentClassifier.classify(chain, duplicate_ack_count=2)
        assert result is not None
        assert result.incident_type == "EXECUTION_DUPLICATION"
        assert result.severity == "critical"

    def test_type_iii_has_priority_over_archived_nodes(self):
        """TYPE III must fire before TYPE I even when archived nodes present."""
        chain = _chain(
            nodes=(_node(outbox_archived=True),),
            chain_complete=False,
        )
        result = CausalIncidentClassifier.classify(chain, duplicate_ack_count=3)
        assert result is not None
        assert result.incident_type == "EXECUTION_DUPLICATION"

    def test_duplicate_ack_count_1_is_not_duplication(self):
        chain = _chain(nodes=(_node(),), chain_complete=True)
        result = CausalIncidentClassifier.classify(chain, duplicate_ack_count=1)
        assert result is None


class TestTypeI:
    def test_archived_node_incomplete_returns_loss_of_causality(self):
        chain = _chain(
            nodes=(_node(outbox_archived=True),),
            chain_complete=False,
            missing_layers=("outbox",),
        )
        result = CausalIncidentClassifier.classify(chain)
        assert result is not None
        assert result.incident_type == "LOSS_OF_CAUSALITY"
        assert result.missing_layers == ("outbox",)

    def test_archived_node_complete_chain_is_not_type_i(self):
        """Archived but complete → chain is fine (evidence via ProcessedEvent)."""
        chain = _chain(nodes=(_node(outbox_archived=True),), chain_complete=True)
        result = CausalIncidentClassifier.classify(chain)
        assert result is None

    def test_type_i_takes_priority_over_type_v(self):
        """When both archived and DLQ nodes present, TYPE I wins."""
        chain = _chain(
            nodes=(
                _node(outbox_archived=True),
                _node(processing_status="dlq"),
            ),
            chain_complete=False,
        )
        result = CausalIncidentClassifier.classify(chain)
        assert result is not None
        assert result.incident_type == "LOSS_OF_CAUSALITY"


class TestTypeV:
    def test_dlq_node_incomplete_no_archive_returns_partial_propagation(self):
        chain = _chain(
            nodes=(_node(processing_status="dlq"),),
            chain_complete=False,
        )
        result = CausalIncidentClassifier.classify(chain)
        assert result is not None
        assert result.incident_type == "PARTIAL_PROPAGATION_FAILURE"

    def test_incomplete_no_dlq_no_archive_returns_none(self):
        """Pending chain with no DLQ and no archive — not yet classifiable."""
        chain = _chain(
            nodes=(_node(processing_status="pending"),),
            chain_complete=False,
        )
        result = CausalIncidentClassifier.classify(chain)
        assert result is None

    def test_complete_chain_with_dlq_node_is_not_type_v(self):
        """If chain_complete=True, partial propagation does not apply."""
        chain = _chain(
            nodes=(_node(processing_status="dlq"),),
            chain_complete=True,
        )
        result = CausalIncidentClassifier.classify(chain)
        assert result is None


class TestTypeIVSilentSkip:
    def test_silent_skip_complete_acked_no_observed_effects(self):
        chain = _chain(nodes=(_node(event_type="finance.transaction"),), chain_complete=True)
        registry = _Registry(
            expected=frozenset({"balance_updated"}),
            observed=frozenset(),
        )
        result = CausalIncidentClassifier.classify(chain, domain_registry=registry)
        assert result is not None
        assert result.incident_type == "SILENT_SKIP"

    def test_no_classification_without_registry(self):
        chain = _chain(nodes=(_node(),), chain_complete=True)
        result = CausalIncidentClassifier.classify(chain)
        assert result is None

    def test_no_classification_when_expected_effects_empty(self):
        """Registry returns empty expected → no opinion → not classifiable."""
        chain = _chain(nodes=(_node(),), chain_complete=True)
        registry = _Registry(expected=frozenset(), observed=frozenset())
        result = CausalIncidentClassifier.classify(chain, domain_registry=registry)
        assert result is None

    def test_incomplete_chain_not_classified_as_silent_skip(self):
        """TYPE IV only fires on complete chains."""
        chain = _chain(
            nodes=(_node(processing_status="pending"),),
            chain_complete=False,
        )
        registry = _Registry(expected=frozenset({"x"}), observed=frozenset())
        result = CausalIncidentClassifier.classify(chain, domain_registry=registry)
        assert result is None


class TestTypeIIDivergence:
    def test_divergence_observed_differs_from_expected(self):
        chain = _chain(nodes=(_node(event_type="finance.transaction"),), chain_complete=True)
        registry = _Registry(
            expected=frozenset({"balance_updated", "audit_logged"}),
            observed=frozenset({"balance_updated"}),  # audit_logged missing
        )
        result = CausalIncidentClassifier.classify(chain, domain_registry=registry)
        assert result is not None
        assert result.incident_type == "DIVERGENCE"

    def test_all_expected_effects_observed_returns_none(self):
        chain = _chain(nodes=(_node(event_type="finance.transaction"),), chain_complete=True)
        registry = _Registry(
            expected=frozenset({"balance_updated"}),
            observed=frozenset({"balance_updated"}),
        )
        result = CausalIncidentClassifier.classify(chain, domain_registry=registry)
        assert result is None


class TestSeverity:
    def test_finance_type_v_escalates_medium_to_critical(self):
        chain = _chain(
            nodes=(_node(event_type="finance.transaction", processing_status="dlq"),),
            chain_complete=False,
        )
        result = CausalIncidentClassifier.classify(chain)
        assert result is not None
        assert result.incident_type == "PARTIAL_PROPAGATION_FAILURE"
        assert result.severity == "critical"

    def test_finance_type_i_stays_critical(self):
        chain = _chain(
            nodes=(_node(event_type="finance.balance", outbox_archived=True),),
            chain_complete=False,
        )
        result = CausalIncidentClassifier.classify(chain)
        assert result is not None
        assert result.severity == "critical"

    def test_non_finance_type_v_is_medium(self):
        chain = _chain(
            nodes=(_node(event_type="contracts.closed", processing_status="dlq"),),
            chain_complete=False,
        )
        result = CausalIncidentClassifier.classify(chain)
        assert result is not None
        assert result.severity == "medium"

    def test_non_finance_type_i_is_high(self):
        chain = _chain(
            nodes=(_node(event_type="contracts.closed", outbox_archived=True),),
            chain_complete=False,
        )
        result = CausalIncidentClassifier.classify(chain)
        assert result is not None
        assert result.severity == "high"

    def test_finance_silent_skip_escalates_medium_to_critical(self):
        chain = _chain(nodes=(_node(event_type="finance.transaction"),), chain_complete=True)
        registry = _Registry(expected=frozenset({"x"}), observed=frozenset())
        result = CausalIncidentClassifier.classify(chain, domain_registry=registry)
        assert result is not None
        assert result.severity == "critical"


class TestAffectedDomains:
    def test_single_domain_extracted_from_event_type(self):
        chain = _chain(nodes=(_node(event_type="finance.transaction", processing_status="dlq"),), chain_complete=False)
        result = CausalIncidentClassifier.classify(chain)
        assert result is not None
        assert result.affected_domains == frozenset({"finance"})

    def test_multi_domain_chain(self):
        chain = _chain(
            nodes=(
                _node(event_type="finance.transaction", outbox_archived=True),
                _node(event_type="contracts.closed", outbox_archived=False),
            ),
            chain_complete=False,
        )
        result = CausalIncidentClassifier.classify(chain)
        assert result is not None
        assert result.affected_domains == frozenset({"finance", "contracts"})

    def test_unknown_domain_when_no_prefix(self):
        chain = _chain(
            nodes=(_node(event_type="noprefixevent", processing_status="dlq"),),
            chain_complete=False,
        )
        result = CausalIncidentClassifier.classify(chain)
        assert result is not None
        assert result.affected_domains == frozenset({"unknown"})


class TestMetadata:
    def test_detected_by_propagated(self):
        chain = _chain(
            nodes=(_node(event_type="contracts.closed", processing_status="dlq"),),
            chain_complete=False,
        )
        result = CausalIncidentClassifier.classify(chain, detected_by="slo_monitor")
        assert result is not None
        assert result.detected_by == "slo_monitor"

    def test_causal_root_id_matches_chain(self):
        root = uuid.uuid4()
        chain = _chain(
            nodes=(_node(outbox_archived=True),),
            chain_complete=False,
            root_id=root,
        )
        result = CausalIncidentClassifier.classify(chain)
        assert result is not None
        assert result.causal_root_id == root

    def test_correlation_id_taken_from_first_node(self):
        chain = _chain(
            nodes=(_node(processing_status="dlq"),),
            chain_complete=False,
        )
        result = CausalIncidentClassifier.classify(chain)
        assert result is not None
        assert result.correlation_id == _CORR

    def test_incident_is_immutable(self):
        chain = _chain(
            nodes=(_node(processing_status="dlq"),),
            chain_complete=False,
        )
        result = CausalIncidentClassifier.classify(chain)
        assert result is not None
        with pytest.raises((AttributeError, TypeError)):
            result.incident_type = "LOSS_OF_CAUSALITY"  # type: ignore[misc]
