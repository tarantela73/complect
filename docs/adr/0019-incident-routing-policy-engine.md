# ADR-0019: Incident Routing Policy Engine (IRPE)

**Date:** 2026-06-03
**Status:** Accepted
**Author(s):** sever
**Deciders:** Backend Platform Team

---

## Context

ADR-0018 introduced a formal causal incident taxonomy: six `IncidentType` values produced by
`CausalIncidentClassifier.classify()` as immutable `CausalIncident` value objects.

The remaining gap: classification without a routing decision is **diagnostic infrastructure,
not operational infrastructure**. A `CausalIncident` that reaches no owner team has no
operational value. Post-ADR-0018 the system can answer "what broke and of what type" but
cannot answer "who owns it, who escalates, and when does the Incident Commander take over."

Additionally, the classification model has a structural limitation: `classify()` returns
exactly one `IncidentType` (the highest-priority match). Real chains can trigger multiple
incident types simultaneously — a **compound incident** — which is unrepresentable in the
1D classification output. Compound incidents require a different routing decision than either
type would receive alone.

**Key constraints:**

- The routing engine must be a pure function: deterministic, no DB queries, fully testable
  without infrastructure
- `IncidentRoutingPolicy` must NOT modify or extend `CausalIncidentClassifier` — classification
  and routing are orthogonal concerns
- Compound incidents are modelled explicitly as `IncidentBundle` — a `frozenset[CausalIncident]`
  over the same `causal_root_id`
- The Incident Commander is a **formal output of the policy**, not a manual escalation fallback
- Routing metadata is emitted as structlog JSON fields (Loki-compatible); Prometheus is not in
  the current infrastructure stack

---

## Options Considered

### Option 1: Routing table as static documentation only

**Description:** Document ownership and escalation in a runbook. Operators consult it manually.

**Pros:**
- Zero implementation cost

**Cons:**
- Routing decision depends on operator recall under incident pressure
- Compound incidents remain unmodelled — no formal cross-type escalation rule
- No machine-readable routing metadata for Loki indexing

**Why ruled out:** Operational topology that lives in human heads is not incident-grade reliability.

### Option 2: Routing logic embedded in the SLO monitor task

**Description:** `run_chain_completeness_monitor_task` determines ownership inline.

**Pros:**
- Single place to change

**Cons:**
- Routing logic not reachable from DLQ inspector, query API, or manual tooling
- Not testable in isolation from the Celery/DB infrastructure
- Violates the principle that routing is a pure function of the incident, not its detection source

**Why ruled out:** Tightly couples routing policy to a single detection trigger.

### Option 3: Dedicated `IncidentRoutingPolicy` as a pure function over `IncidentBundle`

**Description:** `IncidentBundle` wraps a `frozenset[CausalIncident]` for a single `causal_root_id`.
`IncidentRoutingPolicy.route(bundle) → RoutingDecision` is a pure function. Any detection
source (SLO monitor, DLQ inspector, query API) can call it.

**Pros:**
- Pure function: fully testable without DB or Celery
- Compound incidents are first-class: `IncidentBundle.is_compound = len(incidents) > 1`
- Incident Commander is deterministic output of the policy, not manual decision
- structlog_labels emitted as structured routing metadata for any caller

**Cons:**
- Caller is responsible for constructing `IncidentBundle` correctly (same `causal_root_id`)
- Compound incident detection (multiple incident types per chain) requires the call site to
  run the classifier multiple times — this is intentional, not a limitation

**Why chosen:** Correctness, testability, and composability outweigh the call-site contract.

---

## Decision

**We will implement a pure `IncidentRoutingPolicy` engine that accepts an `IncidentBundle`
(one or more `CausalIncident` values sharing a `causal_root_id`) and returns a deterministic
`RoutingDecision` including owner team, escalation path, IC requirement, and structlog labels.**

---

## Models

### IncidentBundle

```python
@dataclass(slots=True, frozen=True)
class IncidentBundle:
    incidents: frozenset[CausalIncident]
    causal_root_id: uuid.UUID
    is_compound: bool            # = len(incidents) > 1
```

`IncidentBundle.from_incidents(incidents, causal_root_id)` is the canonical factory.
All incidents in a bundle MUST share the same `causal_root_id`. Enforcement: call-site contract
(not validated in the dataclass to preserve purity).

### RoutingDecision

```python
@dataclass(slots=True, frozen=True)
class RoutingDecision:
    owner_team: str
    escalation_path: tuple[str, ...]
    requires_incident_commander: bool
    compound: bool
    structlog_labels: dict[str, str]
```

---

## Routing Rules

### 1. Ownership (single incident)

| Incident Type | Base Owner | Finance refinement |
|---|---|---|
| `LOSS_OF_CAUSALITY` | `platform_sre` | append `finance_domain` to escalation path |
| `DIVERGENCE` | `consumer_runtime` | append `finance_domain` to escalation path |
| `EXECUTION_DUPLICATION` | `consumer_runtime` | append `finance_domain` to escalation path |
| `SILENT_SKIP` | `domain_owner` → refined by `affected_domains` | → `finance_domain` if finance |
| `PARTIAL_PROPAGATION_FAILURE` | `platform_sre` | append `finance_domain` |
| `ORDERING_VIOLATION` | `platform_architecture` | append `finance_domain` |

`SILENT_SKIP` domain refinement: `finance` → `finance_domain`; `contracts` → `contracts_domain`;
otherwise `domain_owner`.

### 2. Compound escalation triggers

`requires_incident_commander = True` when bundle is compound AND any of:

```
(DIVERGENCE ∈ types AND EXECUTION_DUPLICATION ∈ types)  -- idempotency violation chain
OR len(union of all affected_domains across bundle) > 1  -- cross-domain
OR (SILENT_SKIP ∈ types AND DIVERGENCE ∈ types)          -- data corruption class
```

When `requires_incident_commander = True`:
- `owner_team = "incident_commander"`
- `escalation_path = union(all individual escalation paths) + ("incident_commander",)`

When compound but no escalation trigger:
- `owner_team` = owner of the highest-severity incident in the bundle
- `escalation_path` = union of all individual escalation paths (no IC appended)

### 3. Critical compound cases

| Bundle composition | Class | Handling |
|---|---|---|
| `DIVERGENCE` + `EXECUTION_DUPLICATION` | Idempotency violation chain | IC mandatory |
| `SILENT_SKIP` + `DIVERGENCE` | Data corruption class | IC mandatory + `incident.class = "data_corruption"` |
| Cross-domain (`finance` + `contracts`) | Cross-domain lineage breach | IC mandatory |

### 4. Escalation paths per type

| Type | Base escalation path |
|---|---|
| `LOSS_OF_CAUSALITY` | `platform_sre → consumer_runtime` |
| `DIVERGENCE` | `consumer_runtime → data_integrity` |
| `EXECUTION_DUPLICATION` | `consumer_runtime → platform_sre` |
| `SILENT_SKIP` | `{domain_owner} → platform_sre` |
| `PARTIAL_PROPAGATION_FAILURE` | `platform_sre → consumer_runtime → {domain_owner}` |
| `ORDERING_VIOLATION` | `platform_architecture → architectural_review` |

Finance domain appended to path when `"finance" ∈ affected_domains` and not already present.

---

## structlog_labels Format

Emitted by every `RoutingDecision` for Loki indexing:

```json
{
  "incident.type": "compound|single",
  "incident.types": "I,IV",
  "incident.critical": "true|false",
  "incident.root": "<uuid>",
  "incident.domains": "finance,contracts",
  "routing.owner": "incident_commander",
  "incident.class": "data_corruption"   // only when TYPE IV + TYPE II
}
```

`incident.types` uses Roman numeral codes (I–VI) matching the taxonomy table from ADR-0018.
`incident.critical = "true"` when `requires_incident_commander = True` OR `incident.class` is set.

---

## Consequences

### Positive Consequences
- Incident Commander is now a deterministic policy output, not operator judgment under pressure
- Compound incidents are first-class values: the call site constructs `IncidentBundle`, IRPE
  handles the rest
- `structlog_labels` enables Loki queries: `{routing.owner="incident_commander"}`,
  `{incident.class="data_corruption"}` — no Prometheus push gateway required
- Any detection source (SLO monitor, DLQ inspector, manual tooling) can produce a routing
  decision without access to DB or Celery

### Negative Consequences / Accepted Tradeoffs
- Call site is responsible for constructing `IncidentBundle` with a consistent `causal_root_id` —
  there is no runtime validation that all incidents share the same root
- Compound incident detection requires running the classifier multiple times per chain
  (once per evidence set). This is by design: classification and routing remain orthogonal.
- `structlog_labels` is `dict[str, str]` — callers must merge into their structlog context
  manually; the IRPE does not call `structlog.bind()` itself

### Risks
- If `DomainEffectRegistry` is not implemented for a domain, TYPE IV is unclassifiable
  (ADR-0018 constraint) — compound data corruption incidents may go undetected. Mitigation:
  contract tests per domain (ADR-0003).
- `ORDERING_VIOLATION` routing is specified but the incident type is `Proposed` (blocked on
  ADR-0012). The routing rules for TYPE VI are complete and will activate automatically when
  ADR-0012 ships.

---

## Implementation Notes

- Module: `apps.common.services.incident_routing`
- `IncidentRoutingPolicy.route()` is a `@staticmethod` with no side effects
- `IncidentBundle.from_incidents()` is the canonical factory; direct construction is allowed
  in tests
- Escalation path deduplication: `dict.fromkeys()` preserves order while removing duplicates
- Finance presence check: `"finance" in incident.affected_domains` — domain prefix from
  `CausalNode.event_type` set by `CausalIncidentClassifier`

---

## Review Date

Review when any of the following occur:
- ADR-0012 ships — TYPE VI routing activates; `platform_architecture` owner path requires
  an actual `architectural_review` integration (PagerDuty, OpsGenie, or equivalent)
- First domain implements `DomainEffectRegistry` — data corruption class detection becomes
  live for Finance; review IC trigger thresholds
- Team topology changes (team names in routing table become stale)
