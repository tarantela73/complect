"""
Pure unit tests for IncidentRoutingPolicy (ADR-0019).
No DB access — IncidentBundle is constructed directly.
"""

import uuid
from datetime import datetime, timezone

import pytest

from apps.common.services.causal_incident import CausalIncident
from apps.common.services.incident_routing import (
    IncidentBundle,
    IncidentContext,
    IncidentRoutingPolicy,
)

_NOW = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)
_ROOT = uuid.uuid4()


def _incident(
    incident_type: str,
    domains: frozenset[str] | None = None,
    severity: str = "high",
    causal_root_id: uuid.UUID | None = None,
) -> CausalIncident:
    return CausalIncident(
        incident_type=incident_type,
        causal_root_id=causal_root_id or _ROOT,
        correlation_id=uuid.uuid4(),
        severity=severity,
        chain_complete=False,
        missing_layers=(),
        affected_domains=domains or frozenset({"contracts"}),
        detected_by="test",
    )


def _bundle(*incidents: CausalIncident, root: uuid.UUID | None = None) -> IncidentBundle:
    return IncidentBundle.from_incidents(frozenset(incidents), root or _ROOT)


class TestSingleIncidentRouting:
    def test_loss_of_causality_routes_to_platform_sre(self):
        b = _bundle(_incident("LOSS_OF_CAUSALITY"))
        d = IncidentRoutingPolicy.route(b)
        assert d.owner_team == "platform_sre"
        assert d.requires_incident_commander is False
        assert d.compound is False

    def test_divergence_routes_to_consumer_runtime(self):
        b = _bundle(_incident("DIVERGENCE"))
        d = IncidentRoutingPolicy.route(b)
        assert d.owner_team == "consumer_runtime"

    def test_execution_duplication_routes_to_consumer_runtime(self):
        b = _bundle(_incident("EXECUTION_DUPLICATION"))
        d = IncidentRoutingPolicy.route(b)
        assert d.owner_team == "consumer_runtime"

    def test_silent_skip_finance_routes_to_finance_domain(self):
        b = _bundle(_incident("SILENT_SKIP", domains=frozenset({"finance"})))
        d = IncidentRoutingPolicy.route(b)
        assert d.owner_team == "finance_domain"

    def test_silent_skip_contracts_routes_to_contracts_domain(self):
        b = _bundle(_incident("SILENT_SKIP", domains=frozenset({"contracts"})))
        d = IncidentRoutingPolicy.route(b)
        assert d.owner_team == "contracts_domain"

    def test_silent_skip_unknown_domain_routes_to_domain_owner(self):
        b = _bundle(_incident("SILENT_SKIP", domains=frozenset({"unknown"})))
        d = IncidentRoutingPolicy.route(b)
        assert d.owner_team == "domain_owner"

    def test_partial_propagation_routes_to_platform_sre(self):
        b = _bundle(_incident("PARTIAL_PROPAGATION_FAILURE"))
        d = IncidentRoutingPolicy.route(b)
        assert d.owner_team == "platform_sre"

    def test_ordering_violation_routes_to_platform_architecture(self):
        b = _bundle(_incident("ORDERING_VIOLATION"))
        d = IncidentRoutingPolicy.route(b)
        assert d.owner_team == "platform_architecture"

    def test_finance_appended_to_escalation_for_loss_of_causality(self):
        b = _bundle(_incident("LOSS_OF_CAUSALITY", domains=frozenset({"finance"})))
        d = IncidentRoutingPolicy.route(b)
        assert "finance_domain" in d.escalation_path
        assert d.escalation_path[-1] == "finance_domain"

    def test_escalation_path_has_no_duplicates(self):
        b = _bundle(_incident("SILENT_SKIP", domains=frozenset({"finance"})))
        d = IncidentRoutingPolicy.route(b)
        assert len(d.escalation_path) == len(set(d.escalation_path))


class TestCompoundRouting:
    def test_is_compound_true_when_multiple_incidents(self):
        b = _bundle(
            _incident("LOSS_OF_CAUSALITY"),
            _incident("PARTIAL_PROPAGATION_FAILURE"),
        )
        assert b.is_compound is True

    def test_is_compound_false_for_single_incident(self):
        b = _bundle(_incident("LOSS_OF_CAUSALITY"))
        assert b.is_compound is False

    def test_type_ii_and_iii_triggers_incident_commander(self):
        b = _bundle(
            _incident("DIVERGENCE"),
            _incident("EXECUTION_DUPLICATION"),
        )
        d = IncidentRoutingPolicy.route(b)
        assert d.owner_team == "incident_commander"
        assert d.requires_incident_commander is True
        assert "incident_commander" in d.escalation_path

    def test_cross_domain_triggers_incident_commander(self):
        b = _bundle(
            _incident("LOSS_OF_CAUSALITY", domains=frozenset({"finance"})),
            _incident("DIVERGENCE", domains=frozenset({"contracts"})),
        )
        d = IncidentRoutingPolicy.route(b)
        assert d.requires_incident_commander is True
        assert d.owner_team == "incident_commander"

    def test_compound_without_trigger_no_incident_commander(self):
        """TYPE I + TYPE V, same domain, no cross-type trigger → no IC."""
        b = _bundle(
            _incident("LOSS_OF_CAUSALITY", domains=frozenset({"contracts"})),
            _incident("PARTIAL_PROPAGATION_FAILURE", domains=frozenset({"contracts"})),
        )
        d = IncidentRoutingPolicy.route(b)
        assert d.requires_incident_commander is False
        assert d.compound is True
        assert d.owner_team != "incident_commander"

    def test_compound_without_trigger_uses_highest_severity_owner(self):
        b = _bundle(
            _incident("LOSS_OF_CAUSALITY", domains=frozenset({"contracts"}), severity="high"),
            _incident("PARTIAL_PROPAGATION_FAILURE", domains=frozenset({"contracts"}), severity="critical"),
        )
        d = IncidentRoutingPolicy.route(b)
        # PARTIAL_PROPAGATION_FAILURE with critical severity → platform_sre owns (it's critical)
        assert d.owner_team == "platform_sre"

    def test_compound_escalation_path_is_union_of_individual(self):
        b = _bundle(
            _incident("DIVERGENCE"),
            _incident("EXECUTION_DUPLICATION"),
        )
        d = IncidentRoutingPolicy.route(b)
        assert "consumer_runtime" in d.escalation_path
        assert "platform_sre" in d.escalation_path
        assert "incident_commander" in d.escalation_path

    def test_incident_commander_last_in_escalation_path(self):
        b = _bundle(
            _incident("DIVERGENCE"),
            _incident("EXECUTION_DUPLICATION"),
        )
        d = IncidentRoutingPolicy.route(b)
        assert d.escalation_path[-1] == "incident_commander"


class TestDataCorruptionClass:
    def test_silent_skip_plus_divergence_is_data_corruption(self):
        b = _bundle(
            _incident("SILENT_SKIP", domains=frozenset({"finance"})),
            _incident("DIVERGENCE", domains=frozenset({"finance"})),
        )
        d = IncidentRoutingPolicy.route(b)
        assert d.structlog_labels.get("incident.class") == "data_corruption"
        assert d.requires_incident_commander is True

    def test_data_corruption_sets_critical_flag(self):
        b = _bundle(
            _incident("SILENT_SKIP", domains=frozenset({"finance"})),
            _incident("DIVERGENCE", domains=frozenset({"finance"})),
        )
        d = IncidentRoutingPolicy.route(b)
        assert d.structlog_labels["incident.critical"] == "true"

    def test_single_silent_skip_not_data_corruption(self):
        b = _bundle(_incident("SILENT_SKIP", domains=frozenset({"finance"})))
        d = IncidentRoutingPolicy.route(b)
        assert "incident.class" not in d.structlog_labels


class TestStructlogLabels:
    def test_single_incident_type_label(self):
        b = _bundle(_incident("LOSS_OF_CAUSALITY"))
        d = IncidentRoutingPolicy.route(b)
        assert d.structlog_labels["incident.type"] == "single"
        assert d.structlog_labels["incident.types"] == "I"

    def test_compound_incident_type_label(self):
        b = _bundle(
            _incident("DIVERGENCE"),
            _incident("EXECUTION_DUPLICATION"),
        )
        d = IncidentRoutingPolicy.route(b)
        assert d.structlog_labels["incident.type"] == "compound"
        assert "II" in d.structlog_labels["incident.types"]
        assert "III" in d.structlog_labels["incident.types"]

    def test_incident_root_in_labels(self):
        root = uuid.uuid4()
        b = IncidentBundle.from_incidents(
            frozenset({_incident("LOSS_OF_CAUSALITY", causal_root_id=root)}),
            root,
        )
        d = IncidentRoutingPolicy.route(b)
        assert d.structlog_labels["incident.root"] == str(root)

    def test_incident_domains_in_labels(self):
        b = _bundle(_incident("LOSS_OF_CAUSALITY", domains=frozenset({"finance"})))
        d = IncidentRoutingPolicy.route(b)
        assert "finance" in d.structlog_labels["incident.domains"]

    def test_routing_owner_in_labels(self):
        b = _bundle(_incident("DIVERGENCE"))
        d = IncidentRoutingPolicy.route(b)
        assert d.structlog_labels["routing.owner"] == "consumer_runtime"

    def test_non_critical_single_incident_label(self):
        b = _bundle(_incident("PARTIAL_PROPAGATION_FAILURE"))
        d = IncidentRoutingPolicy.route(b)
        assert d.structlog_labels["incident.critical"] == "false"

    def test_critical_label_when_ic_required(self):
        b = _bundle(
            _incident("DIVERGENCE"),
            _incident("EXECUTION_DUPLICATION"),
        )
        d = IncidentRoutingPolicy.route(b)
        assert d.structlog_labels["incident.critical"] == "true"


class TestEdgeCases:
    def test_empty_bundle_raises(self):
        with pytest.raises(ValueError, match="empty"):
            IncidentRoutingPolicy.route(IncidentContext(incidents=(), causal_root_id=_ROOT))

    def test_routing_decision_is_immutable(self):
        b = _bundle(_incident("LOSS_OF_CAUSALITY"))
        d = IncidentRoutingPolicy.route(b)
        with pytest.raises((AttributeError, TypeError)):
            d.owner_team = "hacked"  # type: ignore[misc]

    def test_incident_context_is_immutable(self):
        b = _bundle(_incident("LOSS_OF_CAUSALITY"))
        with pytest.raises((AttributeError, TypeError)):
            b.causal_root_id = uuid.uuid4()  # type: ignore[misc]

    def test_from_incidents_factory(self):
        i1 = _incident("LOSS_OF_CAUSALITY")
        i2 = _incident("DIVERGENCE")
        b = IncidentBundle.from_incidents(frozenset({i1, i2}), _ROOT)
        assert b.is_compound is True
        assert b.causal_root_id == _ROOT
        assert len(b.incidents) == 2

    def test_single_incident_from_factory(self):
        b = IncidentBundle.from_incidents(frozenset({_incident("LOSS_OF_CAUSALITY")}), _ROOT)
        assert b.is_compound is False


class TestCompoundNewRule:
    """New compound rule: len(incidents)>1 OR len(domains)>1 OR (divergence AND slo_violated)."""

    def test_single_incident_cross_domain_is_compound(self):
        inc = _incident("DIVERGENCE", domains=frozenset({"finance", "contracts"}))
        ctx = IncidentContext(incidents=(inc,), causal_root_id=_ROOT)
        assert ctx.is_compound is True

    def test_divergence_plus_chain_slo_is_compound(self):
        ctx = IncidentContext(
            incidents=(),
            causal_root_id=_ROOT,
            divergence_state="DETECTABLE",
            chain_slo_violated=True,
        )
        assert ctx.is_compound is True

    def test_divergence_alone_not_compound(self):
        ctx = IncidentContext(
            incidents=(),
            causal_root_id=_ROOT,
            divergence_state="DETECTABLE",
            chain_slo_violated=False,
        )
        assert ctx.is_compound is False

    def test_chain_slo_alone_not_compound(self):
        ctx = IncidentContext(
            incidents=(),
            causal_root_id=_ROOT,
            chain_slo_violated=True,
        )
        assert ctx.is_compound is False

    def test_compound_divergence_plus_slo_structlog_labels(self):
        ctx = IncidentContext(
            incidents=(),
            causal_root_id=_ROOT,
            divergence_state="IRREVERSIBLE",
            chain_slo_violated=True,
            affected_domains=frozenset({"finance"}),
        )
        d = IncidentRoutingPolicy.route(ctx)
        assert d.compound is True
        assert d.structlog_labels["incident.divergence_state"] == "IRREVERSIBLE"
        assert d.structlog_labels["incident.chain_slo_violated"] == "true"


class TestMonitoringOnlyRouting:
    """Routing paths with no CausalIncident objects — SLO monitors and task failures."""

    def test_causal_pair_violation_routes_to_finance(self):
        ctx = IncidentContext(
            incidents=(),
            causal_root_id=_ROOT,
            divergence_state="DETECTABLE",
            causal_pair_violations=frozenset({"contract_closed → finance_transaction_posted"}),
            affected_domains=frozenset({"finance", "contracts"}),
        )
        d = IncidentRoutingPolicy.route(ctx)
        assert d.owner_team == "finance_domain"
        assert "platform_sre" in d.escalation_path

    def test_chain_slo_violated_routes_to_platform_sre(self):
        ctx = IncidentContext(
            incidents=(),
            causal_root_id=_ROOT,
            chain_slo_violated=True,
            affected_domains=frozenset({"infrastructure"}),
        )
        d = IncidentRoutingPolicy.route(ctx)
        assert d.owner_team == "platform_sre"

    def test_pair_violations_in_structlog_labels(self):
        ctx = IncidentContext(
            incidents=(),
            causal_root_id=_ROOT,
            causal_pair_violations=frozenset({"pair_A", "pair_B"}),
            affected_domains=frozenset({"contracts"}),
        )
        d = IncidentRoutingPolicy.route(ctx)
        assert "pair_A" in d.structlog_labels["incident.pair_violations"]
        assert "pair_B" in d.structlog_labels["incident.pair_violations"]

    def test_monitoring_only_never_requires_incident_commander(self):
        ctx = IncidentContext(
            incidents=(),
            causal_root_id=_ROOT,
            divergence_state="DETECTABLE",
            chain_slo_violated=True,
            affected_domains=frozenset({"finance", "contracts"}),
        )
        d = IncidentRoutingPolicy.route(ctx)
        assert d.requires_incident_commander is False

    def test_incident_root_absent_produces_empty_string_label(self):
        ctx = IncidentContext(
            incidents=(),
            chain_slo_violated=True,
            affected_domains=frozenset({"infrastructure"}),
        )
        d = IncidentRoutingPolicy.route(ctx)
        assert d.structlog_labels["incident.root"] == ""
