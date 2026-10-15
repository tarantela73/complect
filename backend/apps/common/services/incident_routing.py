import uuid
from dataclasses import dataclass, field

from apps.common.services.causal_incident import CausalIncident
from apps.common.services.causal_pair_registry import DivergenceState

_TYPE_CODES: dict[str, str] = {
    "LOSS_OF_CAUSALITY": "I",
    "DIVERGENCE": "II",
    "EXECUTION_DUPLICATION": "III",
    "SILENT_SKIP": "IV",
    "PARTIAL_PROPAGATION_FAILURE": "V",
    "ORDERING_VIOLATION": "VI",
}

_BASE_OWNERSHIP: dict[str, str] = {
    "LOSS_OF_CAUSALITY": "platform_sre",
    "DIVERGENCE": "consumer_runtime",
    "EXECUTION_DUPLICATION": "consumer_runtime",
    "SILENT_SKIP": "domain_owner",
    "PARTIAL_PROPAGATION_FAILURE": "platform_sre",
    "ORDERING_VIOLATION": "platform_architecture",
}

_BASE_ESCALATION: dict[str, tuple[str, ...]] = {
    "LOSS_OF_CAUSALITY": ("platform_sre", "consumer_runtime"),
    "DIVERGENCE": ("consumer_runtime", "data_integrity"),
    "EXECUTION_DUPLICATION": ("consumer_runtime", "platform_sre"),
    "SILENT_SKIP": ("domain_owner", "platform_sre"),
    "PARTIAL_PROPAGATION_FAILURE": ("platform_sre", "consumer_runtime", "domain_owner"),
    "ORDERING_VIOLATION": ("platform_architecture", "architectural_review"),
}

_SEVERITY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


@dataclass(slots=True, frozen=True)
class IncidentContext:
    """
    Aggregation of all routing signals from independent runtime sources (ADR-0019).

    Sources:
      - incidents: from CausalIncidentClassifier (TYPE I–VI)
      - divergence_state: from CausalDivergenceClassifier (OBSERVABLE/DETECTABLE/IRREVERSIBLE)
      - chain_slo_violated: from ChainCompletenessMonitor
      - causal_pair_violations: from check_causal_pair_slo_task (pair names)
      - affected_domains: explicit for monitoring-only contexts without incidents

    is_compound is computed in __post_init__ from all signals.
    """

    incidents: tuple[CausalIncident, ...]
    causal_root_id: uuid.UUID | None = None
    divergence_state: DivergenceState | None = None
    chain_slo_violated: bool = False
    causal_pair_violations: frozenset[str] = field(default_factory=frozenset)
    correlation_id: uuid.UUID | None = None
    affected_domains: frozenset[str] = field(default_factory=frozenset)
    is_compound: bool = field(init=False)

    def __post_init__(self) -> None:
        all_doms = _compute_all_domains(self)
        compound = (
            len(self.incidents) > 1
            or len(all_doms) > 1
            or (self.divergence_state is not None and self.chain_slo_violated)
        )
        object.__setattr__(self, "is_compound", compound)

    @classmethod
    def from_incidents(
        cls,
        incidents: frozenset[CausalIncident],
        causal_root_id: uuid.UUID,
    ) -> "IncidentContext":
        return cls(incidents=tuple(incidents), causal_root_id=causal_root_id)


# Backward-compatibility alias
IncidentBundle = IncidentContext


@dataclass(slots=True, frozen=True)
class RoutingDecision:
    owner_team: str
    escalation_path: tuple[str, ...]
    requires_incident_commander: bool
    compound: bool
    structlog_labels: dict[str, str]


def _compute_all_domains(ctx: IncidentContext) -> frozenset[str]:
    from_incidents: frozenset[str] = frozenset().union(*(i.affected_domains for i in ctx.incidents))
    return from_incidents | ctx.affected_domains


def _refine_domain_owner(domains: frozenset[str]) -> str:
    if "finance" in domains:
        return "finance_domain"
    if "contracts" in domains:
        return "contracts_domain"
    return "domain_owner"


def _owner_for(incident: CausalIncident) -> str:
    base = _BASE_OWNERSHIP[incident.incident_type]
    if base == "domain_owner":
        return _refine_domain_owner(incident.affected_domains)
    return base


def _escalation_for(incident: CausalIncident) -> tuple[str, ...]:
    path: list[str] = []
    refined = _refine_domain_owner(incident.affected_domains)
    for step in _BASE_ESCALATION[incident.incident_type]:
        path.append(refined if step == "domain_owner" else step)
    if "finance" in incident.affected_domains and "finance_domain" not in path:
        path.append("finance_domain")
    return tuple(dict.fromkeys(path))


def _union_escalation(ctx: IncidentContext) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for incident in sorted(ctx.incidents, key=lambda i: _SEVERITY_RANK[i.severity], reverse=True):
        for step in _escalation_for(incident):
            seen[step] = None
    return tuple(seen)


def _compound_escalation_triggers(ctx: IncidentContext) -> bool:
    types = {i.incident_type for i in ctx.incidents}
    domains = _compute_all_domains(ctx)
    type_ii_and_iii = "DIVERGENCE" in types and "EXECUTION_DUPLICATION" in types
    cross_domain = len(domains) > 1
    return type_ii_and_iii or cross_domain


def _is_data_corruption(ctx: IncidentContext) -> bool:
    types = {i.incident_type for i in ctx.incidents}
    return "SILENT_SKIP" in types and "DIVERGENCE" in types


def _monitoring_labels(ctx: IncidentContext, owner_team: str) -> dict[str, str]:
    all_doms = _compute_all_domains(ctx)
    labels: dict[str, str] = {
        "incident.type": "compound" if ctx.is_compound else "single",
        "incident.types": "",
        "incident.critical": "false",
        "incident.root": str(ctx.causal_root_id) if ctx.causal_root_id else "",
        "incident.domains": ",".join(sorted(all_doms)),
        "routing.owner": owner_team,
    }
    if ctx.divergence_state:
        labels["incident.divergence_state"] = ctx.divergence_state
    if ctx.chain_slo_violated:
        labels["incident.chain_slo_violated"] = "true"
    if ctx.causal_pair_violations:
        labels["incident.pair_violations"] = ",".join(sorted(ctx.causal_pair_violations))
    return labels


def _route_monitoring_only(ctx: IncidentContext) -> RoutingDecision:
    """Routes contexts with no CausalIncident objects (SLO monitors, task failures)."""
    all_doms = _compute_all_domains(ctx)
    has_finance = "finance" in all_doms
    if has_finance:
        owner_team = "finance_domain"
        escalation: tuple[str, ...] = ("finance_domain", "platform_sre")
    else:
        owner_team = "platform_sre"
        escalation = ("platform_sre",)
    return RoutingDecision(
        owner_team=owner_team,
        escalation_path=escalation,
        requires_incident_commander=False,
        compound=ctx.is_compound,
        structlog_labels=_monitoring_labels(ctx, owner_team),
    )


class IncidentRoutingPolicy:
    """
    Pure routing engine over IncidentContext (ADR-0019).
    Issues no DB queries. Deterministic for the same input.

    Two routing paths:
      - Incident path: CausalIncident objects present → type-based ownership matrix.
      - Monitoring path: no incidents → domain-based routing from explicit signals.
    """

    @staticmethod
    def route(ctx: IncidentContext) -> RoutingDecision:
        if (
            not ctx.incidents
            and not ctx.divergence_state
            and not ctx.chain_slo_violated
            and not ctx.causal_pair_violations
        ):
            raise ValueError("Cannot route empty IncidentContext")

        if not ctx.incidents:
            return _route_monitoring_only(ctx)

        types = {i.incident_type for i in ctx.incidents}
        all_domains = _compute_all_domains(ctx)
        data_corruption = _is_data_corruption(ctx)
        requires_ic = ctx.is_compound and (_compound_escalation_triggers(ctx) or data_corruption)

        if requires_ic:
            owner_team = "incident_commander"
            escalation_path = _union_escalation(ctx) + ("incident_commander",)
        elif ctx.is_compound:
            worst = max(ctx.incidents, key=lambda i: _SEVERITY_RANK[i.severity])
            owner_team = _owner_for(worst)
            escalation_path = _union_escalation(ctx)
        else:
            incident = next(iter(ctx.incidents))
            owner_team = _owner_for(incident)
            escalation_path = _escalation_for(incident)

        is_critical = requires_ic or data_corruption
        labels: dict[str, str] = {
            "incident.type": "compound" if ctx.is_compound else "single",
            "incident.types": ",".join(sorted(_TYPE_CODES.get(t, t) for t in types)),
            "incident.critical": "true" if is_critical else "false",
            "incident.root": str(ctx.causal_root_id) if ctx.causal_root_id else "",
            "incident.domains": ",".join(sorted(all_domains)),
            "routing.owner": owner_team,
        }
        if data_corruption:
            labels["incident.class"] = "data_corruption"
        if ctx.divergence_state:
            labels["incident.divergence_state"] = ctx.divergence_state
        if ctx.chain_slo_violated:
            labels["incident.chain_slo_violated"] = "true"
        if ctx.causal_pair_violations:
            labels["incident.pair_violations"] = ",".join(sorted(ctx.causal_pair_violations))

        return RoutingDecision(
            owner_team=owner_team,
            escalation_path=escalation_path,
            requires_incident_commander=requires_ic,
            compound=ctx.is_compound,
            structlog_labels=labels,
        )
