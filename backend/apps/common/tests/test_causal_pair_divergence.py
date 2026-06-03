"""
Tests for ADR-0014: Cross-Domain Consistency Divergence Model.

Covers:
- CausalDivergenceClassifier: state classification by age and finalization boundary
- CausalPairRegistry: lookup and expected effects
- OutboxBackedDomainEffectRegistry: DB-backed observed effects
- TYPE IV (SILENT_SKIP) and TYPE II (DIVERGENCE) via CausalIncidentClassifier
- CausalPairRepairService: initiate and complete repair records
- check_causal_pair_slo_task: observability-only alerting
- Contract invariants: structural guarantees on CAUSAL_PAIRS
"""
import uuid
from datetime import datetime, timezone

import pytest
import structlog.testing
from django.utils import timezone as tz

from apps.common.models.causal_pair_repair import CausalPairRepairRecord
from apps.common.models.outbox import OutboxRecord
from apps.common.services.causal_incident import CausalIncidentClassifier
from apps.common.services.causal_pair_registry import (
    CAUSAL_PAIRS,
    KNOWN_EVENTS,
    CausalDivergenceClassifier,
    CausalPairDefinition,
    CausalPairRegistry,
    OutboxBackedDomainEffectRegistry,
)
from apps.common.services.causal_pair_repair_service import CausalPairRepairService
from apps.common.services.causal_query import CausalChain, CausalNode
from apps.common.tasks.causal_pair_monitor import check_causal_pair_slo_task

_TENANT = uuid.uuid4()
_NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)

_CONTRACT_CLOSED_PAIR = CausalPairDefinition(
    name="contract_closed → finance_transaction_posted",
    source_event="contracts.ContractClosed",
    expected_event="finance.TransactionPosted",
    correlation_field="contract_id",
    expected_within_seconds=300,
    finalization_boundary_source="finance.AccountingPeriod.is_closed",
    authority_on_conflict="require_human",
    repair_requires_sign_off=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _causal_node(
    event_type: str = "contracts.ContractClosed",
    processing_status: str = "acked",
    outbox_archived: bool = False,
) -> CausalNode:
    return CausalNode(
        outbox_record_id=uuid.uuid4(),
        causal_root_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        tenant_id=_TENANT,
        occurred_at=_NOW,
        event_type=event_type,
        aggregate_id=str(uuid.uuid4()),
        aggregate_type="contracts.contract",
        aggregate_seq=None,
        processing_status=processing_status,
        outbox_archived=outbox_archived,
    )


def _chain(*nodes: CausalNode, chain_complete: bool = True) -> CausalChain:
    return CausalChain(
        root_id=uuid.uuid4(),
        nodes=tuple(nodes),
        chain_complete=chain_complete,
        missing_layers=(),
        queried_at=_NOW,
        queried_by="test",
    )


class _MockDomainRegistry:
    def __init__(self, expected: frozenset[str], observed: frozenset[str]):
        self._expected = expected
        self._observed = observed

    def expected_effects(self, event_type: str) -> frozenset[str]:
        return self._expected if event_type == "contracts.ContractClosed" else frozenset()

    def observed_effects(self, causal_root_id: uuid.UUID) -> frozenset[str]:
        return self._observed


def _make_outbox(
    event_type: str,
    *,
    causal_root_id: uuid.UUID | None = None,
    is_processed: bool = True,
    failed_permanently: bool = False,
    days_old: int = 0,
    seconds_old: int = 0,
) -> OutboxRecord:
    from datetime import timedelta

    record = OutboxRecord(
        event_type=event_type,
        payload_json={"tenant_id": str(_TENANT)},
        correlation_id=uuid.uuid4(),
        causal_root_id=causal_root_id,
        is_processed=is_processed,
        failed_permanently_at=tz.now() if failed_permanently else None,
    )
    record.save()
    past = tz.now() - timedelta(days=days_old, seconds=seconds_old)
    OutboxRecord.objects.filter(id=record.id).update(
        created_at=past,
        processed_at=past if is_processed else None,
    )
    return OutboxRecord.objects.get(id=record.id)


# ---------------------------------------------------------------------------
# TestCausalDivergenceClassifier
# ---------------------------------------------------------------------------

class TestCausalDivergenceClassifier:

    def test_observable_within_slo(self):
        from datetime import timedelta
        source_created_at = _NOW - timedelta(seconds=100)
        state = CausalDivergenceClassifier.classify(
            _CONTRACT_CLOSED_PAIR,
            source_created_at=source_created_at,
            reference_time=_NOW,
        )
        assert state == "OBSERVABLE"

    def test_observable_at_slo_boundary(self):
        from datetime import timedelta
        source_created_at = _NOW - timedelta(seconds=300)
        state = CausalDivergenceClassifier.classify(
            _CONTRACT_CLOSED_PAIR,
            source_created_at=source_created_at,
            reference_time=_NOW,
        )
        assert state == "OBSERVABLE"

    def test_detectable_beyond_slo_in_retention_window(self):
        from datetime import timedelta
        source_created_at = _NOW - timedelta(seconds=301)
        state = CausalDivergenceClassifier.classify(
            _CONTRACT_CLOSED_PAIR,
            source_created_at=source_created_at,
            reference_time=_NOW,
        )
        assert state == "DETECTABLE"

    def test_detectable_just_within_retention(self):
        from datetime import timedelta
        # 29 days: beyond SLO (300s), within 30d retention window.
        source_created_at = _NOW - timedelta(days=29)
        state = CausalDivergenceClassifier.classify(
            _CONTRACT_CLOSED_PAIR,
            source_created_at=source_created_at,
            reference_time=_NOW,
        )
        assert state == "DETECTABLE"

    def test_irreversible_beyond_retention_window(self):
        from datetime import timedelta
        source_created_at = _NOW - timedelta(days=31)
        state = CausalDivergenceClassifier.classify(
            _CONTRACT_CLOSED_PAIR,
            source_created_at=source_created_at,
            reference_time=_NOW,
        )
        assert state == "IRREVERSIBLE"

    def test_irreversible_when_finalization_boundary_closed(self):
        from datetime import timedelta

        class _ClosedBoundary:
            def is_finalized(self, v: str) -> bool:
                return True

        # Even within SLO (100s old), finalization overrides to IRREVERSIBLE.
        source_created_at = _NOW - timedelta(seconds=100)
        state = CausalDivergenceClassifier.classify(
            _CONTRACT_CLOSED_PAIR,
            source_created_at=source_created_at,
            finalization_boundary=_ClosedBoundary(),
            correlation_field_value="contract-123",
            reference_time=_NOW,
        )
        assert state == "IRREVERSIBLE"

    def test_open_finalization_boundary_does_not_force_irreversible(self):
        from datetime import timedelta

        class _OpenBoundary:
            def is_finalized(self, v: str) -> bool:
                return False

        source_created_at = _NOW - timedelta(seconds=400)
        state = CausalDivergenceClassifier.classify(
            _CONTRACT_CLOSED_PAIR,
            source_created_at=source_created_at,
            finalization_boundary=_OpenBoundary(),
            correlation_field_value="contract-123",
            reference_time=_NOW,
        )
        assert state == "DETECTABLE"

    def test_boundary_without_correlation_value_is_ignored(self):
        from datetime import timedelta

        class _ClosedBoundary:
            def is_finalized(self, v: str) -> bool:
                return True

        # boundary provided but no correlation_field_value → ignored, uses time-based.
        source_created_at = _NOW - timedelta(seconds=400)
        state = CausalDivergenceClassifier.classify(
            _CONTRACT_CLOSED_PAIR,
            source_created_at=source_created_at,
            finalization_boundary=_ClosedBoundary(),
            correlation_field_value=None,  # no value provided
            reference_time=_NOW,
        )
        assert state == "DETECTABLE"


# ---------------------------------------------------------------------------
# TestCausalPairRegistry
# ---------------------------------------------------------------------------

class TestCausalPairRegistry:

    def test_get_by_source_known_event(self):
        pair = CausalPairRegistry.get_by_source("contracts.ContractClosed")
        assert pair is not None
        assert pair.expected_event == "finance.TransactionPosted"

    def test_get_by_source_unknown_returns_none(self):
        assert CausalPairRegistry.get_by_source("unknown.Event") is None

    def test_expected_effects_for_known_source(self):
        effects = CausalPairRegistry.expected_effects_for("contracts.ContractClosed")
        assert effects == frozenset({"finance.TransactionPosted"})

    def test_expected_effects_for_expected_event_has_no_pair(self):
        # finance.TransactionPosted is a destination, not a source — no pair registered.
        effects = CausalPairRegistry.expected_effects_for("finance.TransactionPosted")
        assert effects == frozenset()

    def test_expected_effects_unknown_event_returns_empty(self):
        assert CausalPairRegistry.expected_effects_for("no.such.event") == frozenset()


# ---------------------------------------------------------------------------
# TestOutboxBackedDomainEffectRegistry
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestOutboxBackedDomainEffectRegistry:

    def test_expected_effects_for_registered_source(self):
        registry = OutboxBackedDomainEffectRegistry()
        assert registry.expected_effects("contracts.ContractClosed") == frozenset(
            {"finance.TransactionPosted"}
        )

    def test_expected_effects_unknown_returns_empty(self):
        registry = OutboxBackedDomainEffectRegistry()
        assert registry.expected_effects("unknown.Event") == frozenset()

    def test_observed_effects_returns_downstream_event_types(self):
        root_id = uuid.uuid4()
        source = _make_outbox("contracts.ContractClosed", causal_root_id=root_id)
        # Manually set source.causal_root_id = source.id (normal flow).
        OutboxRecord.objects.filter(id=source.id).update(causal_root_id=source.id)
        _make_outbox("finance.TransactionPosted", causal_root_id=source.id)

        registry = OutboxBackedDomainEffectRegistry()
        observed = registry.observed_effects(source.id)
        assert observed == frozenset({"finance.TransactionPosted"})

    def test_observed_effects_excludes_root_itself(self):
        source = _make_outbox("contracts.ContractClosed")
        OutboxRecord.objects.filter(id=source.id).update(causal_root_id=source.id)

        registry = OutboxBackedDomainEffectRegistry()
        observed = registry.observed_effects(source.id)
        # Root's own event_type must not appear in observed effects.
        assert "contracts.ContractClosed" not in observed

    def test_observed_effects_excludes_permanently_failed(self):
        source = _make_outbox("contracts.ContractClosed")
        OutboxRecord.objects.filter(id=source.id).update(causal_root_id=source.id)
        _make_outbox(
            "finance.TransactionPosted",
            causal_root_id=source.id,
            failed_permanently=True,
        )

        registry = OutboxBackedDomainEffectRegistry()
        observed = registry.observed_effects(source.id)
        # DLQ records (failed_permanently_at IS NOT NULL) are not observed effects.
        assert observed == frozenset()

    def test_observed_effects_empty_for_unknown_root(self):
        registry = OutboxBackedDomainEffectRegistry()
        assert registry.observed_effects(uuid.uuid4()) == frozenset()

    def test_observed_effects_multiple_downstream_types(self):
        source = _make_outbox("contracts.ContractClosed")
        OutboxRecord.objects.filter(id=source.id).update(causal_root_id=source.id)
        _make_outbox("finance.TransactionPosted", causal_root_id=source.id)
        _make_outbox("notifications.ContractClosedNotification", causal_root_id=source.id)

        registry = OutboxBackedDomainEffectRegistry()
        observed = registry.observed_effects(source.id)
        assert "finance.TransactionPosted" in observed
        assert "notifications.ContractClosedNotification" in observed


# ---------------------------------------------------------------------------
# TestTypeIVAndIIViaRegistry
# ---------------------------------------------------------------------------

class TestTypeIVAndIIViaRegistry:

    def test_type_iv_silent_skip_when_no_downstream_effects(self):
        node = _causal_node(event_type="contracts.ContractClosed", processing_status="acked")
        chain = _chain(node, chain_complete=True)
        registry = _MockDomainRegistry(
            expected=frozenset({"finance.TransactionPosted"}),
            observed=frozenset(),
        )
        incident = CausalIncidentClassifier.classify(chain, domain_registry=registry)
        assert incident is not None
        assert incident.incident_type == "SILENT_SKIP"

    def test_type_ii_divergence_when_observed_differs(self):
        node = _causal_node(event_type="contracts.ContractClosed", processing_status="acked")
        chain = _chain(node, chain_complete=True)
        registry = _MockDomainRegistry(
            expected=frozenset({"finance.TransactionPosted"}),
            observed=frozenset({"notifications.Sent"}),
        )
        incident = CausalIncidentClassifier.classify(chain, domain_registry=registry)
        assert incident is not None
        assert incident.incident_type == "DIVERGENCE"

    def test_no_incident_when_expected_effects_observed(self):
        node = _causal_node(event_type="contracts.ContractClosed", processing_status="acked")
        chain = _chain(node, chain_complete=True)
        registry = _MockDomainRegistry(
            expected=frozenset({"finance.TransactionPosted"}),
            observed=frozenset({"finance.TransactionPosted"}),
        )
        incident = CausalIncidentClassifier.classify(chain, domain_registry=registry)
        assert incident is None

    def test_no_classification_for_unregistered_source_event(self):
        node = _causal_node(event_type="reporting.ReportGenerated", processing_status="acked")
        chain = _chain(node, chain_complete=True)
        registry = _MockDomainRegistry(
            expected=frozenset(),
            observed=frozenset(),
        )
        incident = CausalIncidentClassifier.classify(chain, domain_registry=registry)
        assert incident is None

    def test_type_v_partial_propagation_takes_priority_over_iv(self):
        # DLQ node + incomplete chain → TYPE V fires before domain registry check.
        source_node = _causal_node(
            event_type="contracts.ContractClosed", processing_status="acked"
        )
        dlq_node = _causal_node(
            event_type="finance.TransactionPosted", processing_status="dlq"
        )
        chain = _chain(source_node, dlq_node, chain_complete=False)
        registry = _MockDomainRegistry(
            expected=frozenset({"finance.TransactionPosted"}),
            observed=frozenset(),
        )
        incident = CausalIncidentClassifier.classify(chain, domain_registry=registry)
        assert incident is not None
        assert incident.incident_type == "PARTIAL_PROPAGATION_FAILURE"

    def test_domain_registry_skipped_when_chain_incomplete(self):
        # Incomplete chain → priority 5 (domain registry) is skipped.
        node = _causal_node(event_type="contracts.ContractClosed", processing_status="claimed")
        chain = _chain(node, chain_complete=False)
        registry = _MockDomainRegistry(
            expected=frozenset({"finance.TransactionPosted"}),
            observed=frozenset(),
        )
        incident = CausalIncidentClassifier.classify(chain, domain_registry=registry)
        # No incident: incomplete chain with no DLQ/archived nodes = unclassified.
        assert incident is None


# ---------------------------------------------------------------------------
# TestCausalPairRepairService
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCausalPairRepairService:

    def test_initiate_repair_creates_record(self):
        source_id = uuid.uuid4()
        record = CausalPairRepairService.initiate_repair(
            pair_name="contract_closed → finance_transaction_posted",
            source_event_id=source_id,
            source_event_type="contracts.ContractClosed",
            expected_event_type="finance.TransactionPosted",
            divergence_state="DETECTABLE",
            initiated_by="ops-001",
        )
        assert CausalPairRepairRecord.objects.filter(id=record.id).exists()

    def test_initiate_repair_fields_set_correctly(self):
        source_id = uuid.uuid4()
        record = CausalPairRepairService.initiate_repair(
            pair_name="contract_closed → finance_transaction_posted",
            source_event_id=source_id,
            source_event_type="contracts.ContractClosed",
            expected_event_type="finance.TransactionPosted",
            divergence_state="DETECTABLE",
            initiated_by="ops-007",
            notes="Confirmed missing posting for contract X",
        )
        assert record.divergence_state == "DETECTABLE"
        assert record.repair_initiated_by == "ops-007"
        assert record.notes == "Confirmed missing posting for contract X"
        assert record.repair_completed_at is None
        assert record.sign_off_by is None

    def test_complete_repair_sets_completion_fields(self):
        source_id = uuid.uuid4()
        record = CausalPairRepairService.initiate_repair(
            pair_name="contract_closed → finance_transaction_posted",
            source_event_id=source_id,
            source_event_type="contracts.ContractClosed",
            expected_event_type="finance.TransactionPosted",
            divergence_state="DETECTABLE",
            initiated_by="ops-001",
        )
        comp_tx_id = uuid.uuid4()
        CausalPairRepairService.complete_repair(
            repair_record_id=record.id,
            compensating_transaction_id=comp_tx_id,
            sign_off_by="finance-lead",
        )
        updated = CausalPairRepairRecord.objects.get(id=record.id)
        assert updated.repair_completed_at is not None
        assert updated.compensating_transaction_id == comp_tx_id
        assert updated.sign_off_by == "finance-lead"
        assert updated.sign_off_at is not None

    def test_complete_repair_raises_on_already_completed(self):
        source_id = uuid.uuid4()
        record = CausalPairRepairService.initiate_repair(
            pair_name="contract_closed → finance_transaction_posted",
            source_event_id=source_id,
            source_event_type="contracts.ContractClosed",
            expected_event_type="finance.TransactionPosted",
            divergence_state="DETECTABLE",
            initiated_by="ops-001",
        )
        CausalPairRepairService.complete_repair(
            repair_record_id=record.id,
            compensating_transaction_id=uuid.uuid4(),
            sign_off_by="finance-lead",
        )
        with pytest.raises(ValueError, match="already completed"):
            CausalPairRepairService.complete_repair(
                repair_record_id=record.id,
                compensating_transaction_id=uuid.uuid4(),
                sign_off_by="finance-lead",
            )

    def test_complete_repair_raises_for_unknown_id(self):
        with pytest.raises(ValueError, match="not found or already completed"):
            CausalPairRepairService.complete_repair(
                repair_record_id=uuid.uuid4(),
                compensating_transaction_id=uuid.uuid4(),
                sign_off_by="ops-001",
            )


# ---------------------------------------------------------------------------
# TestCheckCausalPairSloTask
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCheckCausalPairSloTask:

    def test_task_logs_warning_when_expected_event_absent(self):
        # Source event older than SLO (300s), no downstream record.
        source = _make_outbox(
            "contracts.ContractClosed",
            is_processed=True,
            seconds_old=600,
        )
        OutboxRecord.objects.filter(id=source.id).update(causal_root_id=source.id)

        with structlog.testing.capture_logs() as logs:
            check_causal_pair_slo_task()

        violation_logs = [l for l in logs if l.get("event") == "causal_pair_slo_violation"]
        assert len(violation_logs) >= 1
        log = violation_logs[0]
        assert log["pair_name"] == "contract_closed → finance_transaction_posted"
        assert log["divergence_state"] == "DETECTABLE"
        assert log["source_outbox_id"] == str(source.id)

    def test_task_no_alert_when_expected_event_present(self):
        source = _make_outbox(
            "contracts.ContractClosed",
            is_processed=True,
            seconds_old=600,
        )
        OutboxRecord.objects.filter(id=source.id).update(causal_root_id=source.id)
        # Downstream event present — no violation.
        _make_outbox("finance.TransactionPosted", causal_root_id=source.id)

        with structlog.testing.capture_logs() as logs:
            check_causal_pair_slo_task()

        violation_logs = [
            l for l in logs
            if l.get("event") == "causal_pair_slo_violation"
            and l.get("source_outbox_id") == str(source.id)
        ]
        assert len(violation_logs) == 0

    def test_task_no_alert_for_recent_source_within_slo(self):
        # Source created 60 seconds ago — within SLO (300s), no alert.
        source = _make_outbox(
            "contracts.ContractClosed",
            is_processed=True,
            seconds_old=60,
        )
        OutboxRecord.objects.filter(id=source.id).update(causal_root_id=source.id)

        with structlog.testing.capture_logs() as logs:
            check_causal_pair_slo_task()

        violation_logs = [
            l for l in logs
            if l.get("event") == "causal_pair_slo_violation"
            and l.get("source_outbox_id") == str(source.id)
        ]
        assert len(violation_logs) == 0


# ---------------------------------------------------------------------------
# TestCausalPairContractInvariants
# ---------------------------------------------------------------------------

class TestCausalPairContractInvariants:

    def test_all_pairs_have_nonempty_required_fields(self):
        for pair in CAUSAL_PAIRS:
            assert pair.name, f"{pair} has empty name"
            assert pair.source_event, f"{pair} has empty source_event"
            assert pair.expected_event, f"{pair} has empty expected_event"
            assert pair.correlation_field, f"{pair} has empty correlation_field"
            assert pair.expected_within_seconds > 0, f"{pair} has non-positive SLO"
            assert pair.finalization_boundary_source, f"{pair} missing finalization_boundary_source"
            assert pair.authority_on_conflict in (
                "finance", "contracts", "require_human"
            ), f"{pair} has invalid authority_on_conflict"

    def test_finance_flows_require_sign_off(self):
        for pair in CAUSAL_PAIRS:
            if pair.authority_on_conflict in ("finance", "require_human"):
                assert pair.repair_requires_sign_off, (
                    f"{pair.name} involves Finance authority but repair_requires_sign_off=False"
                )

    def test_all_pair_event_types_in_known_events(self):
        for pair in CAUSAL_PAIRS:
            assert pair.source_event in KNOWN_EVENTS, (
                f"{pair.source_event} not in KNOWN_EVENTS — add it when registering the pair"
            )
            assert pair.expected_event in KNOWN_EVENTS, (
                f"{pair.expected_event} not in KNOWN_EVENTS — add it when registering the pair"
            )

    def test_pair_names_unique(self):
        names = [p.name for p in CAUSAL_PAIRS]
        assert len(names) == len(set(names)), "Duplicate pair names in CAUSAL_PAIRS"

    def test_source_events_unique_per_expected(self):
        # One source event maps to at most one expected event in the registry.
        # If a source has multiple expected effects, CausalPairDefinition must be extended.
        sources = [p.source_event for p in CAUSAL_PAIRS]
        assert len(sources) == len(set(sources)), (
            "Duplicate source_event in CAUSAL_PAIRS — "
            "extend CausalPairDefinition for multi-effect sources"
        )
