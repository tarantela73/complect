# ADR-0017: Chain Completeness SLO

**Date:** 2026-06-03
**Status:** Accepted
**Author(s):** sever
**Deciders:** Backend Platform Team

---

## Context

ADR-0015 (Causal Contract Layer) and ADR-0016 (Causal Query Semantics) established a two-dimensional
causal model: every `OutboxRecord` carries a `causal_root_id` that links it to its logical chain.
`CausalChain.chain_complete: bool` is already a first-class citizen in the query surface — absence of
data is explicitly documented state.

The causal model is now structurally complete, but there is no SLO measuring its runtime health.
Without a completeness metric we cannot:

- alert when consumer ACK latency degrades
- distinguish "healthy repair chain in progress" from "stuck chain leaking indefinitely"
- produce an error budget to gate cross-domain reconciliation decisions (ADR-0014)

**Key constraints:**
- Measurement must not introduce N+1 queries — chains can number in the thousands per observation window
- `aggregate_seq` does not yet exist on `OutboxRecord` (blocked on ADR-0012) — Class C (aggregate history) chains cannot be measured until ADR-0012 ships; the SLO is intentionally scoped to Classes A and B
- DLQ'd records (`failed_permanently_at IS NOT NULL`) represent a separate failure mode; they are excluded from the completeness SLO and tracked by the DLQ rate alert
- The SLO metric is emitted as a structured log event — external aggregation (Grafana Loki / Prometheus Loki exporter) is out of scope for this ADR

---

## Options Considered

### Option 1: Per-query `chain_complete` flag already in CausalChain (status quo)

**Description:** `CausalQueryService.get_chain_by_root()` already returns `chain_complete`.
Trust callers to surface incompleteness.

**Pros:**
- Zero implementation cost

**Cons:**
- No proactive alerting — completeness only observed when someone queries
- No aggregate SLO — no way to measure "what fraction of chains are complete at time T"

**Why ruled out:** Reactive, not proactive. Does not enable error budget tracking.

### Option 2: Periodic Celery task — bulk measurement every N seconds

**Description:** A Celery beat task queries all chains older than the SLO window and not yet
complete, then emits a structured log metric.

**Pros:**
- Proactive: alert fires without anyone querying
- O(1) query depth regardless of chain count (3 fixed queries)
- No schema changes — uses existing `causal_root_id` columns

**Cons:**
- Measurement granularity bounded by beat interval (60s)
- Observation window introduces up to `slack` seconds of lag before a stuck chain alerts

**Why chosen:** Correct trade-off for an event-processing system. The 60s alert lag is acceptable
given the 5-minute SLO window.

### Option 3: Real-time Prometheus counter incremented on ACK

**Description:** Increment a Prometheus counter on every `ConsumerIdempotencyService.ack()` and
decrement a gauge on `claim()`. Compute completeness from the ratio.

**Pros:**
- Sub-second resolution

**Cons:**
- Gauge diverges on consumer crash / process restart (not durable)
- Requires Prometheus push gateway in current stack (not available)
- Counter model cannot represent multi-node chain completeness

**Why ruled out:** Counter semantics cannot model chain completeness atomically.

---

## Decision

**We will implement a periodic Celery task (`run_chain_completeness_monitor_task`) that measures
causal chain completeness against a 5-minute SLO window using a 3-query bulk algorithm.**

The task runs every 60 seconds on the `outbox` queue. It observes all `OutboxRecord` chains
(by `causal_root_id`) created in the interval `[now - SLO_WINDOW - OBSERVATION_SLACK, now - SLO_WINDOW]`.
A chain is "complete" when every non-DLQ'd node in the chain has a corresponding `ProcessedEvent`
with `acked_at IS NOT NULL`. Completeness rate and SLO violation are emitted as structured log events.

---

## SLO Definition

| Attribute | Value |
| --- | --- |
| **SLO name** | `chain_completeness` |
| **SLO target** | 99.9% of chains complete within the SLO window |
| **SLO window** | `CHAIN_COMPLETENESS_SLO_WINDOW_SECONDS` (default: 300s / 5 minutes) |
| **Observation slack** | `CHAIN_COMPLETENESS_OBSERVATION_SLACK_SECONDS` (default: 300s) |
| **Measurement interval** | 60 seconds (Celery beat, `outbox` queue) |
| **Alert event** | `chain_completeness_slo_violated` (structlog ERROR) |

### What counts as "complete"

A causal chain identified by `causal_root_id = R` is complete when:

```
∀ record ∈ OutboxRecord WHERE (causal_root_id = R OR id = R)
    AND failed_permanently_at IS NULL:
    ∃ pe ∈ ProcessedEvent WHERE pe.outbox_record_id = record.id AND pe.acked_at IS NOT NULL
```

### What is excluded

- Records with `failed_permanently_at IS NOT NULL` — tracked by DLQ rate SLO (ADR-0004).
- Records created less than `SLO_WINDOW` seconds ago — still within the allowed completion time.
- Records without `causal_root_id` (pre-ADR-0015) — treated as self-rooted single-node chains.

---

## Invariants

**Invariant 1 — Observation window coverage:** The task observes records created in
`[now - SLO_WINDOW - OBSERVATION_SLACK, now - SLO_WINDOW]`. Any record in this band has
already exceeded the SLO deadline and must be acked or DLQ'd.

**Invariant 2 — Bulk query bound:** The implementation executes exactly 3 DB queries per
invocation regardless of the number of chains observed. No N+1.

**Invariant 3 — DLQ exclusion:** A chain is not counted as "incomplete" because one of its
nodes is DLQ'd. DLQ'd nodes are excluded from the completeness denominator; their repair is
tracked separately via ADR-0013.

**Invariant 4 — Compensation chain completeness:** A repair chain (multiple `OutboxRecord`
nodes sharing a `causal_root_id`) is only complete when ALL non-DLQ'd nodes are acked.
Partial ACK does not count.

---

## Consequences

### Positive Consequences
- Proactive SLO alerting without manual causal queries
- Error budget is now computable from log data
- Foundation for cross-domain reconciliation SLO (ADR-0014 needs a completeness signal)
- No schema changes, no new DB tables

### Negative Consequences / Accepted Tradeoffs
- 60s measurement granularity means a chain stuck for 4:59 does not alert until the next tick
- `OBSERVATION_SLACK` means we never alert on the most recently crossed SLO boundary (by design — prevent false positives from scheduler jitter)
- Class C (aggregate history) chains excluded until ADR-0012 lands

### Risks
- If `outbox` queue is saturated, the monitor task may be delayed — completeness alert fires late. Mitigation: `outbox` queue has `PREFETCH_MULTIPLIER=1`, monitor task is short-lived.
- High chain volume (thousands of unique `causal_root_id` per window) may produce large IN clauses. Mitigation: if IN-list exceeds 10000, fall back to a JOIN-based subquery. Not implemented in Phase 1.

---

## Implementation Notes

- Queue: `outbox` (high-priority, per rule 4.15)
- Task: `apps.common.tasks.chain_completeness_monitor.run_chain_completeness_monitor_task`
- Beat key: `chain-completeness-monitor` (every 60 seconds)
- Settings:
  - `CHAIN_COMPLETENESS_SLO_WINDOW_SECONDS = 300`
  - `CHAIN_COMPLETENESS_OBSERVATION_SLACK_SECONDS = 300`
  - `CHAIN_COMPLETENESS_SLO_TARGET = 0.999`
- Structured log events emitted:
  - `chain_completeness_measured` (INFO or WARNING) — every invocation
  - `chain_completeness_slo_violated` (ERROR) — only when `completeness_rate < SLO_TARGET`

---

## Review Date

Review if any of the following occur:
- ADR-0012 ships `aggregate_seq` — Class C chains can then be added to the SLO
- ADR-0013 ships archive table — archived records must be excluded from the denominator
- `OBSERVATION_SLACK` needs tuning based on observed scheduler jitter in production
