---
date: 2026-07-07
topic: scheduled-task-reliability
---

# Scheduled Task Reliability

## Summary

Make a scheduled (Celery beat) task that fails repeatedly visible to a human within minutes, and stop the class of code bug that silently broke one from shipping again. Deliver both prevention and detection as one reliability effort, reusing the existing failure-capture infrastructure rather than building a new one.

## Problem Frame

A manual product pulse — not any alert — surfaced that `apps/site/tasks/publisher.py::publish_scheduled_pages_task` (beat `cms-publish-scheduled`, every 5 min) had been failing on **every** run with `TransactionManagementError: select_for_update cannot be used outside of a transaction`. The task evaluates a `select_for_update()` queryset outside an `atomic()` block, so it raises before doing any work; CMS scheduled-page publishing was fully non-functional and no one knew.

The project already has mature failure-capture: `BaseEnterpriseTask.on_failure` writes every failure to a DLQ Redis stream (`infrastructure:dlq:stream`) with full context and logs a routed incident (`task_failure_routed`, owner label); `/ops/` exposes DLQ depth and SLO statuses; Grafana/Prometheus run in the stack. The gap is the **last mile and the shape of the signal**: nothing pushes to a human, and DLQ depth is undifferentiated, so a beat task failing 100% of runs looks like slow queue churn (~288 entries/day against a 10 000 cap) rather than a broken job. Separately, the task shipped with **no test that ever invoked it**, so the runtime error Django would raise immediately was never triggered in CI.

## Key Decisions

- **Both axes, one effort.** Prevention (stop the bug class at CI) and detection (surface a repeatedly-failing task to a human) ship together as one reliability initiative — a guardrail without detection still goes blind to non-code failures, and detection without a guardrail lets the same bug recur.
- **Reuse existing infrastructure, do not rebuild.** Detection extends the `on_failure` hook and routes through the existing notifications domain (in-app / email / telegram, rule 4.15). It does not introduce a parallel alerting stack. The DLQ, incident-routing policy, and `/ops/` metrics stay as-is.
- **Detection is per-task, not aggregate.** The load-bearing new signal is "*this named task* is failing persistently," keyed on `task_name` — the dimension the current DLQ-depth metric collapses.
- **Alerts are deduplicated by incident, not by failure.** A task failing every 5 minutes produces one alert per incident with a cooldown, not one per tick, and clears when the task next succeeds.
- **Prevention's highest-leverage form is execution coverage, not static analysis.** Every beat task gets a CI smoke test that invokes it; a scheduled task that raises on a normal run fails CI. This catches the `select_for_update`-outside-`atomic` bug and the broader class of "scheduled task cannot even start," which a pattern-specific lint would miss.
- **Sequencing (queue order):** (1) fix the live `publish_scheduled_pages_task` defect immediately — it is a trivial code change and can ship independently; (2) detection, because it closes the exact "invisible for 24h" gap for any failure cause; (3) prevention smoke-tests + the `select_for_update` site audit. Detection precedes prevention on leverage: it is the broad net; the smoke tests are the narrower CI catch.

## Requirements

**Live defect (prerequisite)**

- R1. `publish_scheduled_pages_task` must complete a normal run without raising: the `select_for_update` fetch must execute inside a transaction. This is the validation case for the whole initiative, not part of its build.

**Detection**

- R2. When a beat/scheduled task fails on consecutive runs past a sensitivity threshold, the system raises exactly one human-visible alert per incident through the notifications domain, carrying the task name, exception class, and failure count.
- R3. A subsequent successful run of that task clears the incident state so a later recurrence alerts again.
- R4. Repeated failures of the same task within an active incident do not emit repeated alerts (cooldown-bounded), so a 5-minute task failing all day yields one alert, not ~288.
- R5. Detection covers every task routed through `BaseEnterpriseTask`, keyed on `task_name`; it does not depend on the failing task's own domain or queue.
- R6. Detection must not itself be an unmonitored scheduled task that can fail silently — its own health is observable without depending on the mechanism it guards.

**Prevention**

- R7. Every task in `CELERY_BEAT_SCHEDULE` has a CI test that invokes it against a minimal/empty database; a task that raises on a normal run fails CI.
- R8. Audit the 14 `select_for_update` call sites for atomic-block safety; each must run inside a transaction or be fixed. (Enumerated in Sources.)

## Key Flows

- F1. **Detection.** **Trigger:** a task raises and Celery invokes `on_failure`. The hook records the failure keyed on `task_name` and increments a per-task failure count. **Threshold crossed:** if the task has failed on consecutive runs past the sensitivity threshold and no incident is active, open an incident and emit one alert via the notifications domain. **Recovery:** the task's next success clears the count and closes the incident. **Covers R2, R3, R4, R5.**

- F2. **Prevention.** **Trigger:** CI runs the test suite. Each registered beat task is invoked once against a minimal DB. **Outcome:** a task that raises on a normal run (e.g., `select_for_update` outside `atomic`) fails the build before merge. **Covers R7.**

## Acceptance Examples

- AE1. **Covers R2.** A 5-minute beat task starts failing every run; within the threshold window (target: a few cycles, not hours) one alert reaches the ops channel naming the task and exception.
- AE2. **Covers R3, R4.** The same task keeps failing for the rest of the day: no further alerts fire. It is then fixed and next run succeeds; a fresh failure days later alerts again.
- AE3. **Covers R2 sensitivity.** A task fails once (transient broker blip) then succeeds: no alert — a single failure is below threshold.
- AE4. **Covers R7.** A change reintroduces `select_for_update` outside `atomic` in any beat task: CI fails on that task's smoke test, before merge.

## Success Criteria

- A persistently-failing scheduled task produces a human-visible, deduplicated alert within minutes — the `cms-publish-scheduled` incident would have surfaced on its own, without a manual pulse.
- Reintroducing the `select_for_update`-outside-`atomic` pattern in a scheduled task fails CI.
- The live publisher defect is fixed and its recurrence is caught by both the CI smoke test (at commit) and the runtime alert (in production).

## Scope Boundaries

**In v1**
- Per-task repeated-failure detection with a deduplicated alert to one ops channel.
- CI smoke-test coverage for every beat task + the 14-site `select_for_update` audit.
- The live `publish_scheduled_pages_task` fix as the validation case.

**Deferred (later, not v1)**
- Owner-routed paging / on-call rotation escalation (the incident-routing policy already assigns an owner label; turning it into a page is a later step).
- Per-task SLO definitions and success-rate dashboards.
- Automatic replay/repair of failed scheduled tasks from the DLQ.
- Multi-channel escalation ladders.
- A static/AST lint for `select_for_update`-outside-`atomic` (smoke tests cover the need for v1).

**Outside this effort**
- The DLQ, causal-chain monitors, and SLO-compliance machinery — they work for their domains and are reused, not replaced.

## Dependencies / Assumptions

- Reuses `BaseEnterpriseTask.on_failure` (`apps/common/tasks/__init__.py`), the notifications domain, and Redis. Assumes `on_failure` fires reliably for scheduled-task failures — true for the observed incident.
- Assumes the ops team consumes an in-app / email / telegram channel; the exact default channel is a planning choice.

## Outstanding Questions

**Deferred to Planning**
- Threshold policy across cadences: a fixed "N consecutive failures" catches a 5-minute task fast but a daily task slowly. Planning decides whether the threshold is cadence-aware (fail-for-T-minutes) or a per-task-configurable count.
- Alert channel default and cooldown/incident-state TTL values.
- Where per-task failure state lives and how R6 (watcher-of-the-watcher) is satisfied — a hook-side counter avoids a new failing beat task; planning confirms the mechanism.

## Sources / Research

- `apps/site/tasks/publisher.py:27-33` — the defect: `list(...select_for_update(skip_locked=True)[:50])` outside `atomic`; `atomic()` sits at line 40 inside the per-page loop, never reached.
- `apps/common/tasks/__init__.py` — `BaseEnterpriseTask.on_failure`: DLQ stream write + `_emit_routing_decision` → `task_failure_routed` log. The detection hook point.
- `config/settings/base.py:177-227` — `CELERY_BEAT_SCHEDULE`, 12 tasks; `cms-publish-scheduled` at 300s, `queue="default"`.
- `apps/common/services/dlq_service.py`, `apps/common/slo/metrics.py::collect_dlq_count`, `apps/common/api/ops.py` — existing DLQ read/replay and the `/ops/` snapshot (DLQ depth + SLO statuses).
- `select_for_update` audit set (R8): `apps/tasks/services/task_service.py:218`, `apps/contracts/repositories/contract_repo.py:26`, `apps/payments/tasks.py:63`, `apps/contragents/api.py:125,159`, `apps/finance/services/transaction_posting.py:42,95,103`, `apps/common/security/audit.py:46`, `apps/payments/services/matching_service.py:35`, `apps/common/tasks/outbox_dispatcher.py:73`, `apps/common/tasks/retention_manager.py:38`, `apps/site/tasks/publisher.py:32`, `apps/site/services/cms_publish_service.py:37`.
