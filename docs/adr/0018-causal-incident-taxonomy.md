# ADR-0018: Causal Incident Taxonomy

**Date:** 2026-06-03
**Status:** Accepted (TYPE VI: Proposed — blocked on ADR-0012)
**Author(s):** sever
**Deciders:** Backend Platform Team

---

## Context

ADR-0015 through ADR-0017 established a complete causal observability stack:

| ADR | Layer |
|-----|-------|
| 0015 | Causal identity: `causal_root_id` propagation across OutboxRecord → ProcessedEvent → DLQ → replay |
| 0016 | Causal query semantics: `CausalChain`, `chain_complete` as first-class citizen |
| 0017 | Causal SLO: `ChainCompletenessMonitor`, 99.9% completeness target, 3-query bulk algorithm |

The resulting gap: the system has the data to reason about causality, but it has no semantics for
what **form of failure** has occurred when a chain is incomplete or diverges. Production incidents
currently require manual interpretation: read the chain, run SQL, decide.

This ADR defines **Causal Incident Taxonomy** — a formal classification of failure modes expressible
over `CausalChain`. The goal is not logging; it is a pure classification function that maps
observable chain state to a machine-readable incident type. The incident type determines:

- alert routing (SLO monitor → ops, divergence → domain team, duplication → security)
- repair class (ADR-0013 handles TYPE I/V, ADR-0014 handles TYPE II)
- severity weight for the Finance domain

**Key constraints:**

- Classification must be a pure function over `CausalChain` + optional domain evidence; no
  additional DB queries inside the classifier
- TYPE IV (Silent Skip) requires domain knowledge: a `DomainEffectRegistry` contract that
  declares expected effects per `event_type`. Without the registry, TYPE IV is unclassifiable
- TYPE III (Execution Duplication) requires a `duplicate_ack_count` signal from the call site —
  `CausalChain` does not carry per-record ACK count
- TYPE VI (Ordering Violation) is structurally blocked on ADR-0012 (`aggregate_seq` field on
  `OutboxRecord`); it is specified here as `Proposed` for forward compatibility
- `affected_domains` is `frozenset[str]`, not `Literal` — a causal chain can span multiple
  domains and the information must not be collapsed to `"mixed"`

---

## Options Considered

### Option 1: Ad-hoc classification in each alert handler (status quo)

**Description:** Each alert (SLO monitor, DLQ inspector, replay engine) applies its own
interpretation of the chain state without a shared taxonomy.

**Pros:**
- Zero implementation cost

**Cons:**
- Same chain classified differently by different components
- No machine-readable incident type for routing or reporting
- postmortem analysis stays manual

**Why ruled out:** Non-uniform semantics across the system. Incident type becomes implicit
context in operator heads rather than a first-class data value.

### Option 2: Enum tag on `CausalChain` set at query time

**Description:** `CausalQueryService` sets an `incident_type` field on `CausalChain` when
it detects anomalies during chain reconstruction.

**Pros:**
- Single point of classification

**Cons:**
- Mixes query semantics (what happened) with incident semantics (what type of failure)
- `CausalQueryService` would need domain effect knowledge to classify TYPE II/IV — violating
  the boundary between common infrastructure and domain layer

**Why ruled out:** Domain-layer concerns leak into infrastructure. `CausalChain` becomes
non-orthogonal — a chain reconstructed for diagnostic purposes would carry incident tags
even when no incident is being evaluated.

### Option 3: Dedicated classifier as a pure function over CausalChain + domain evidence

**Description:** `CausalIncidentClassifier.classify()` is a pure classification function.
`DomainEffectRegistry` is a `typing.Protocol` that each domain may implement to supply
TYPE II/IV evidence. The classifier carries no state and issues no DB queries.

**Pros:**
- Testable in isolation without a DB (pure function)
- Domain layer controls TYPE II/IV detectability via optional registry
- Classifier can be composed with any detection source (SLO monitor, DLQ inspector, query API)
- `CausalIncident` is an immutable value object: safe to log, serialize, and pass through queues

**Cons:**
- TYPE III requires `duplicate_ack_count` pre-fetched by the call site — classifier cannot derive it
- TYPE IV is silently unclassifiable without `DomainEffectRegistry` — callers must understand the
  contract to avoid false negatives

**Why chosen:** Correctness and testability outweigh the call-site contract complexity. A
false negative (unclassified incident) is safer than a false positive (wrong incident type).

---

## Decision

**We will implement a pure classification engine (`CausalIncidentClassifier`) operating over
`CausalChain` values, a `DomainEffectRegistry` Protocol for TYPE II/IV domain evidence, and
a `CausalIncident` frozen dataclass as the output value object.**

---

## Incident Taxonomy

### Formal Notation

```
chain_nodes     = CausalChain.nodes
acked_nodes     = { n ∈ chain_nodes | n.processing_status == "acked" }
dlq_nodes       = { n ∈ chain_nodes | n.processing_status == "dlq" }
archived_nodes  = { n ∈ chain_nodes | n.outbox_archived == True }
missing_layers  = CausalChain.missing_layers
chain_complete  = CausalChain.chain_complete
```

---

### TYPE I — Loss of Causality

**Invariant:**
```
archived_nodes ≠ ∅
AND chain_complete = False
```

**Meaning:** One or more OutboxRecord nodes were archived (ADR-0013 retention) before the
chain was fully processed. The causal graph has a permanent data gap. This is not recoverable
without the archive table.

**Severity:** `high` (Finance: `critical`)

**Repair class:** ADR-0013 — archive recovery. If archive table is present and `archived_at`
is within retention window, records can be restored. Beyond retention: `IRREVERSIBLE`.

**Typical cause:** Archive task ran against a chain that had `failed_permanently_at` set on
a predecessor node, triggering early cleanup before the repair path was exercised.

---

### TYPE II — Divergence

**Invariant:**
```
chain_complete = True
AND DomainEffectRegistry provides non-empty expected_effects(event_type)
AND observed_effects ∩ expected_effects = ∅
```

**Meaning:** The causal chain is complete (all nodes acked), but the domain state does not
reflect the expected downstream effect. Example: `finance.transaction` acked, but balance
unchanged. Domain authority is Finance (ADR-0014).

**Severity:** `high` (Finance: `critical`)

**Repair class:** ADR-0014 — compensating append. Never backward mutation.

**TYPE II is domain-dependent:** Only classifiable when `DomainEffectRegistry` provides a
non-empty expectation set for the event type. Without the registry, this type cannot be raised.

---

### TYPE III — Execution Duplication

**Invariant:**
```
duplicate_ack_count > 1
AND observed_effects ≥ 1
```

**Meaning:** The same causal chain was processed and acknowledged more than once, producing
multiple side effects. Idempotency broken at the consumer layer.

**Severity:** `critical` (all domains — side effects cannot be undone without explicit audit)

**Repair class:** Manual + ADR-0014 divergence audit. Identify duplicate effects via
`CausalQueryService.get_chain_by_root()`, then apply compensating append per domain authority
matrix.

**Distinguishing TYPE III from TYPE IV:**

| Condition | Classification |
|-----------|---------------|
| `acked_count > 1` AND `observed_effects ≥ 1` | TYPE III |
| `acked_count = 1` AND `observed_effects = 0` | TYPE IV |
| `acked_count > 1` AND `observed_effects = 0` | TYPE III (ack duplication without visible effect — possible idempotent consumer) |

Without `observed_effects` evidence from `DomainEffectRegistry`, TYPE III and TYPE IV are
indistinguishable when `acked_count = 1`. The classifier returns `None` rather than guessing.

---

### TYPE IV — Silent Skip

**Invariant:**
```
chain_complete = True
AND acked_count = 1
AND DomainEffectRegistry provides non-empty expected_effects(event_type)
AND observed_effects = ∅
```

**Meaning:** The system considers the chain complete, but no downstream business effect was
observed. The consumer acked the event without producing the required side effect.

**Severity:** `medium` (Finance: `critical`)

**Repair class:** Consumer logic audit. Effect may have been skipped due to a business rule
guard that silently returned without error (e.g., idempotent guard eating the event).

**TYPE IV is domain-dependent (Invariant — non-negotiable):** This type MUST NOT be raised
without a `DomainEffectRegistry` providing a non-empty `expected_effects` set for the
event type. Without the registry, this type is `UNCLASSIFIABLE`.

---

### TYPE V — Partial Propagation Failure

**Invariant:**
```
dlq_nodes ≠ ∅
AND chain_complete = False
AND archived_nodes = ∅
AND causal_root_id preserved across all dlq_nodes
```

**Meaning:** The propagation layer functioned correctly (causal_root_id was preserved through
DLQ), but the causal graph is not yet closed. This is a temporal gap, not a data loss. The
chain may complete after replay (ADR-0013).

**Severity:** `medium` (Finance: `high`)

**Repair class:** ADR-0013 DLQ replay. `AdminDlqManagementService.execute_task_replay()` +
`mark_repaired(operator_id)` after chain closure confirmed.

**Distinguishing TYPE I from TYPE V:**

| Dimension | TYPE I | TYPE V |
|-----------|--------|--------|
| Data gap | Yes — OutboxRecord archived | No — all nodes present |
| causal_root_id continuity | May be broken | Preserved |
| Recoverability | Requires archive recovery | Replay sufficient |
| Signal | `archived_nodes ≠ ∅` | `dlq_nodes ≠ ∅ AND archived_nodes = ∅` |

---

### TYPE VI — Ordering Violation *(Proposed — blocked on ADR-0012)*

**Invariant:**
```
chain_complete = True
AND all nodes acked
AND ∃ (n_i, n_j) ∈ chain_nodes : n_i.aggregate_seq > n_j.aggregate_seq
                                   AND n_i.occurred_at < n_j.occurred_at
```

**Meaning:** The causal chain is complete, but events were processed out of their declared
aggregate sequence order. Finance domain: this can cause incorrect balance recompute.

**Status:** `Proposed`. Not implementable until ADR-0012 ships `aggregate_seq` on
`OutboxRecord`. The classifier returns `None` for this case in Phase 1.

**Severity (future):** `critical` (Finance domain mandatory). Other domains: `high`.

---

## DomainEffectRegistry Protocol

```python
class DomainEffectRegistry(Protocol):
    def expected_effects(self, event_type: str) -> frozenset[str]:
        """
        Returns the set of effect identifiers expected for this event_type.
        Returns frozenset() when this registry has no opinion on the event_type
        (not: "effects expected but not observed" — "no expectation registered").
        """
        ...

    def observed_effects(self, causal_root_id: uuid.UUID) -> frozenset[str]:
        """
        Returns the set of effect identifiers observed for this causal chain.
        Domain-specific probe — may query DB, cache, or external state.
        Called only when expected_effects() returns a non-empty set.
        """
        ...
```

Each domain that wants TYPE II/IV classification must implement this Protocol and pass
the registry to `CausalIncidentClassifier.classify()`. The classifier never imports domain code.

---

## CausalIncident Value Object

```python
@dataclass(slots=True, frozen=True)
class CausalIncident:
    incident_type: IncidentType
    causal_root_id: uuid.UUID
    correlation_id: uuid.UUID | None
    severity: Severity
    chain_complete: bool
    missing_layers: tuple[str, ...]
    affected_domains: frozenset[str]   # e.g. frozenset({"finance", "contracts"})
    detected_by: str
```

`affected_domains` is **always `frozenset[str]`** — never a `Literal` enum. Reason: a
causal chain in the cross-domain Finance→Contracts flow spans two domains and the information
must not be collapsed. This field drives alert routing and SLO attribution.

---

## Classification Priority

When multiple types could apply, use this priority order (highest first):

1. **Empty chain** → `None` (not an incident — insufficient data)
2. **TYPE III** — if `duplicate_ack_count > 1` (data integrity, highest urgency)
3. **TYPE I** — if `archived_nodes ≠ ∅` (data loss)
4. **TYPE V** — if `dlq_nodes ≠ ∅` (propagation stall, potentially recoverable)
5. **TYPE II / TYPE IV** — if domain registry provided and chain complete (domain-layer divergence)
6. **TYPE VI** — future (after ADR-0012), if ordering violation detected
7. **Unclassified** → `None`

---

## Consequences

### Positive Consequences
- Incidents are first-class immutable values: loggable, serializable, routeable
- Domain teams control TYPE II/IV detectability; infrastructure has no domain imports
- TYPE I vs TYPE V boundary is now formal: repair path is unambiguous per type
- Error budget feed: `chain_completeness_slo_violated` (ADR-0017) + incident type → ops routing table

### Negative Consequences / Accepted Tradeoffs
- TYPE IV false negatives until `DomainEffectRegistry` is implemented per domain — this is
  accepted: unclassified is safer than mis-classified
- TYPE III requires `duplicate_ack_count` from call site — implicit call-site contract that must
  be documented per integration point
- TYPE VI cannot be raised until ADR-0012 ships — Finance ordering correctness remains manually
  monitored until then

### Risks
- If `DomainEffectRegistry.observed_effects()` is slow (external DB query), it will block the
  classification caller. Mitigation: callers are responsible for caching; classifier is pure.
- Over time, domains may implement `DomainEffectRegistry` inconsistently. Mitigation: contract
  tests per domain (ADR-0003 snapshot pattern).

---

## Implementation Notes

- Module: `apps.common.services.causal_incident`
- `CausalIncidentClassifier.classify()` is a `@staticmethod` with no DB access
- Domain severity override: Finance domain events (`event_type` prefix `"finance."`) escalate
  `medium` → `critical` and `high` → `critical`
- `detected_by` identifies the caller: `"slo_monitor"`, `"dlq_inspector"`, `"query_api"`, `"manual"`
- `affected_domains` derived from `CausalNode.event_type` prefix (`"finance.transaction"` → `"finance"`)
  with fallback to `frozenset({"unknown"})` when prefix is absent

---

## Review Date

Review when any of the following occur:
- ADR-0012 ships `aggregate_seq` — enables TYPE VI implementation
- ADR-0013 ships archive table — TYPE I repair path becomes concrete
- First domain implements `DomainEffectRegistry` — TYPE II/IV test coverage must follow
- Finance domain goes to production — TYPE III/IV severity mapping requires audit sign-off
