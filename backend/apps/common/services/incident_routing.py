import uuid
from dataclasses import dataclass

from apps.common.services.causal_incident import CausalIncident

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
class IncidentBundle:
    incidents: frozenset[CausalIncident]
    causal_root_id: uuid.UUID
    is_compound: bool

    @classmethod
    def from_incidents(
        cls,
        incidents: frozenset[CausalIncident],
        causal_root_id: uuid.UUID,
    ) -> "IncidentBundle":
        return cls(
            incidents=incidents,
            causal_root_id=causal_root_id,
            is_compound=len(incidents) > 1,
        )


@dataclass(slots=True, frozen=True)
class RoutingDecision:
    owner_team: str
    escalation_path: tuple[str, ...]
    requires_incident_commander: bool
    compound: bool
    structlog_labels: dict[str, str]


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


def _union_escalation(bundle: IncidentBundle) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for incident in sorted(bundle.incidents, key=lambda i: _SEVERITY_RANK[i.severity], reverse=True):
        for step in _escalation_for(incident):
            seen[step] = None
    return tuple(seen)


def _compound_escalation_triggers(bundle: IncidentBundle) -> bool:
    types = {i.incident_type for i in bundle.incidents}
    domains: frozenset[str] = frozenset().union(*(i.affected_domains for i in bundle.incidents))
    type_ii_and_iii = "DIVERGENCE" in types and "EXECUTION_DUPLICATION" in types
    cross_domain = len(domains) > 1
    return type_ii_and_iii or cross_domain


def _is_data_corruption(bundle: IncidentBundle) -> bool:
    types = {i.incident_type for i in bundle.incidents}
    return "SILENT_SKIP" in types and "DIVERGENCE" in types


class IncidentRoutingPolicy:
    """
    Pure routing engine over IncidentBundle (ADR-0019).
    Issues no DB queries. Deterministic for the same input.
    """

    @staticmethod
    def route(bundle: IncidentBundle) -> RoutingDecision:
        if not bundle.incidents:
            raise ValueError("Cannot route empty IncidentBundle")

        types = {i.incident_type for i in bundle.incidents}
        all_domains: frozenset[str] = frozenset().union(*(i.affected_domains for i in bundle.incidents))
        data_corruption = _is_data_corruption(bundle)
        requires_ic = bundle.is_compound and (
            _compound_escalation_triggers(bundle) or data_corruption
        )

        if requires_ic:
            owner_team = "incident_commander"
            escalation_path = _union_escalation(bundle) + ("incident_commander",)
        elif bundle.is_compound:
            worst = max(bundle.incidents, key=lambda i: _SEVERITY_RANK[i.severity])
            owner_team = _owner_for(worst)
            escalation_path = _union_escalation(bundle)
        else:
            incident = next(iter(bundle.incidents))
            owner_team = _owner_for(incident)
            escalation_path = _escalation_for(incident)

        is_critical = requires_ic or data_corruption
        labels: dict[str, str] = {
            "incident.type": "compound" if bundle.is_compound else "single",
            "incident.types": ",".join(sorted(_TYPE_CODES.get(t, t) for t in types)),
            "incident.critical": "true" if is_critical else "false",
            "incident.root": str(bundle.causal_root_id),
            "incident.domains": ",".join(sorted(all_domains)),
            "routing.owner": owner_team,
        }
        if data_corruption:
            labels["incident.class"] = "data_corruption"

        return RoutingDecision(
            owner_team=owner_team,
            escalation_path=escalation_path,
            requires_incident_commander=requires_ic,
            compound=bundle.is_compound,
            structlog_labels=labels,
        )
