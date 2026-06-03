import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

from apps.common.services.causal_query import CausalChain

IncidentType = Literal[
    "LOSS_OF_CAUSALITY",
    "DIVERGENCE",
    "EXECUTION_DUPLICATION",
    "SILENT_SKIP",
    "PARTIAL_PROPAGATION_FAILURE",
    "ORDERING_VIOLATION",
]

Severity = Literal["low", "medium", "high", "critical"]

_FINANCE_PREFIX = "finance."
_FINANCE_ESCALATION: dict[Severity, Severity] = {
    "low": "high",
    "medium": "critical",
    "high": "critical",
    "critical": "critical",
}

_BASE_SEVERITY: dict[IncidentType, Severity] = {
    "LOSS_OF_CAUSALITY": "high",
    "DIVERGENCE": "high",
    "EXECUTION_DUPLICATION": "critical",
    "SILENT_SKIP": "medium",
    "PARTIAL_PROPAGATION_FAILURE": "medium",
    "ORDERING_VIOLATION": "critical",
}


@dataclass(slots=True, frozen=True)
class CausalIncident:
    incident_type: IncidentType
    causal_root_id: uuid.UUID
    correlation_id: uuid.UUID | None
    severity: Severity
    chain_complete: bool
    missing_layers: tuple[str, ...]
    affected_domains: frozenset[str]
    detected_by: str


class DomainEffectRegistry(Protocol):
    """
    Domain contract for TYPE II / TYPE IV classification (ADR-0018).

    Returns frozenset() when no expectation is registered for event_type —
    NOT "effects expected but absent". The classifier treats frozenset() as
    "no opinion → cannot classify TYPE II/IV".
    """

    def expected_effects(self, event_type: str) -> frozenset[str]: ...

    def observed_effects(self, causal_root_id: uuid.UUID) -> frozenset[str]: ...


def _extract_domains(chain: CausalChain) -> frozenset[str]:
    domains: set[str] = set()
    for node in chain.nodes:
        parts = node.event_type.split(".", 1)
        if len(parts) == 2:
            domains.add(parts[0])
    return frozenset(domains) if domains else frozenset({"unknown"})


def _is_finance_chain(chain: CausalChain) -> bool:
    return any(node.event_type.startswith(_FINANCE_PREFIX) for node in chain.nodes)


def _severity(incident_type: IncidentType, chain: CausalChain) -> Severity:
    base = _BASE_SEVERITY[incident_type]
    if _is_finance_chain(chain):
        return _FINANCE_ESCALATION[base]
    return base


class CausalIncidentClassifier:
    """
    Pure classification engine over CausalChain (ADR-0018).

    Issues no DB queries. All evidence must be pre-fetched by the caller.

    Classification priority (highest first):
      1. Empty chain                → None
      2. EXECUTION_DUPLICATION      → duplicate_ack_count > 1
      3. LOSS_OF_CAUSALITY          → archived_nodes present, chain incomplete
      4. PARTIAL_PROPAGATION_FAILURE→ dlq_nodes present, no archived nodes
      5. DIVERGENCE / SILENT_SKIP   → domain registry provided, chain complete
      6. ORDERING_VIOLATION         → future (ADR-0012), always None in Phase 1
      7. Unclassified               → None
    """

    @staticmethod
    def classify(
        chain: CausalChain,
        *,
        duplicate_ack_count: int | None = None,
        domain_registry: DomainEffectRegistry | None = None,
        detected_by: str = "classifier",
    ) -> CausalIncident | None:
        if not chain.nodes:
            return None

        correlation_id: uuid.UUID | None = chain.nodes[0].correlation_id if chain.nodes else None
        affected_domains = _extract_domains(chain)

        def _make(incident_type: IncidentType) -> CausalIncident:
            return CausalIncident(
                incident_type=incident_type,
                causal_root_id=chain.root_id,
                correlation_id=correlation_id,
                severity=_severity(incident_type, chain),
                chain_complete=chain.chain_complete,
                missing_layers=chain.missing_layers,
                affected_domains=affected_domains,
                detected_by=detected_by,
            )

        # Priority 2: Execution duplication — data integrity, highest urgency.
        if duplicate_ack_count is not None and duplicate_ack_count > 1:
            return _make("EXECUTION_DUPLICATION")

        archived_nodes = [n for n in chain.nodes if n.outbox_archived]
        dlq_nodes = [n for n in chain.nodes if n.processing_status == "dlq"]

        # Priority 3: Loss of causality — archived nodes + incomplete chain (data loss).
        if archived_nodes and not chain.chain_complete:
            return _make("LOSS_OF_CAUSALITY")

        # Priority 4: Partial propagation failure — DLQ present, no data loss, causal_root preserved.
        # Invariant: archived_nodes = ∅ (checked above) + dlq_nodes ≠ ∅.
        if dlq_nodes and not chain.chain_complete:
            return _make("PARTIAL_PROPAGATION_FAILURE")

        # Priority 5: Domain-level divergence — requires DomainEffectRegistry.
        # Only evaluated when chain is complete (all nodes acked or structurally present).
        if chain.chain_complete and domain_registry is not None:
            event_types = {node.event_type for node in chain.nodes}
            for event_type in event_types:
                expected = domain_registry.expected_effects(event_type)
                if not expected:
                    continue
                observed = domain_registry.observed_effects(chain.root_id)
                if not observed:
                    # acked_count=1, no observed effects → SILENT_SKIP.
                    return _make("SILENT_SKIP")
                if observed != expected:
                    # Effects observed but diverge from expected → DIVERGENCE.
                    return _make("DIVERGENCE")

        # Priority 6: Ordering violation — blocked on ADR-0012 (aggregate_seq unavailable).
        # aggregate_seq is None on all nodes until ADR-0012 ships.

        return None
