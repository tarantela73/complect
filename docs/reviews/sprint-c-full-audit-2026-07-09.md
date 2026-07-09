# Code Review Results — Full Audit: Sprint C Operational Maturity

**Scope:** merge-base `10c4400` (main) -> `781f511` (HEAD, feat/sprint-c-operational-maturity) — 369 files, +33490/-356 lines, 49 commits
**Intent:** Sprint C — Tasks & Comments domain (CQRS + Outbox notifications), Director Dashboard KPIs, CMS/public site with Turnstile lead form, GIS/Map search, Requests domain (lead workflow), Reports export (Excel/PDF), payments reconciliation parser fixes, RBAC hardening. Requirements tracked in `docs/plans/2026-06-23-001-feat-sprint-c-operational-maturity-plan.md` (R1-R6) plus carried-over Sprint A/B plans.
**Mode:** interactive, **report-only by explicit user instruction** — no fixes applied, working tree untouched (Stage 5c skipped).
**Note on execution:** Parallel persona sub-agent dispatch (`use_subagents`) failed on all 5 waves (HTTP 402 — insufficient OpenRouter credits). Fell back to sequential single-agent review per SKILL.md Fallback clause, applying each persona's lens directly against the diff with targeted `git show`/`grep`/`read_file` inspection instead of full-file dumps.

**Reviewers (persona lenses applied sequentially):** correctness, testing, maintainability, project-standards, security, performance, api-contract, data-migration, reliability, adversarial, agent-native, learnings-researcher
- security — RBAC enforcement mode, IDOR history, Turnstile public endpoint, tenant-scoped mutations
- data-migration — 29 migration files across common/contracts/contragents/objects/requests/site/tasks; live migration-linter run
- reliability — read-your-own-writes vs replica routing, cursor pagination anchor handling, outbox publish ordering
- api-contract — TS SDK freshness vs Pydantic schemas, list response shape consistency
- adversarial — diff far exceeds 200-line "deep" threshold; auth/tenant/data-mutation domains touched throughout

**Critical context:** This branch already has a prior review — `docs/reviews/sprint-c-code-review-2026-06-24.md` (2 P0 + 14 P1, 2026-06-24), and 5 subsequent commits claim fixes (`88feae8`, `531e7c6`, `d535929`, `6287624`, `30ccc24`). This audit re-verifies every prior finding against current `HEAD` rather than trusting commit messages, in addition to reviewing the full branch and the ~90 files added since that review (tasks comments, requests domain, reports, CMS gallery/revisions).

---

### Prior-Review Verification (docs/reviews/sprint-c-code-review-2026-06-24.md)

| ID | Prior finding | Current status |
|----|---------------|-----------------|
| SEC-1 (P0) | `RBAC_ENFORCEMENT_MODE='LOG_ONLY'` in prod | **FIXED** — `base.py:337`=`ENFORCE`, `prod.py:10`=`ENFORCE`, `local.py:22`=`LOG_ONLY` (explicit dev override). Matches `docs/solutions/security-issues/rbac-enforcement-mode-silent-bypass.md`. |
| STD-1 (P0) | `director_queries.py` imports 4 domains' ORM models directly | **FIXED** — now only re-exports domain-owned query functions (`get_open_contracts_count`, `get_objects_summary`, `get_payments_month_summary`), zero direct model imports. |
| SEC-2 (P1) | `detach_file()` no ownership check (IDOR) | **FIXED** — `attachment.attached_by_id != actor_id` check present + test `test_detach_by_non_owner_returns_403`. |
| ADV-3/SEC-6 (P1) | `assigned_to_id` accepted without tenant check | **FIXED** — `user_exists_in_tenant(assigned_to_id, tenant_id)` guard in both `create()` and `update()`. |
| STD-2 (P1) | Raw `attachment.save()` in `detach_file()`, no CAS | **FIXED** — now `EnterpriseSreRepository.atomic_cas_update(...)` inside `transaction.atomic()`. |
| REL-4 (P1) | `TaskCompletedEvent`/`TaskCancelledEvent` missing `tenant_id` | **FIXED** — both dataclasses carry `tenant_id: uuid.UUID`. |
| LRN-2 (P1) | Partial unique index missing `tenant_id` (cross-tenant collision) | **FIXED** — migration `0004_taskattachment_fix_unique_idx_tenant.py` rebuilds index as `(task_id, file_id, tenant_id)`. |
| API-1 (P1) | TS SDK stale, `tenant_id` mismatch on `TaskOut` | **FIXED differently** — `tenant_id` removed from `TaskOut` entirely (backend schema and generated `.d.ts` now agree: no `tenant_id` field). Also incidentally closes SEC-8 below. |
| MIG-1 (P3) | `Task.Meta.indexes` missing explicit `name=` | **FIXED** — `tasks_tenant_status_idx`, `tasks_tenant_assigned_idx` now named. |
| SEC-8 (P3) | `TaskOut` discloses `tenant_id` to client | **FIXED** — same change as API-1 above. |
| TST-1/TST-2 (P1) | No test for `TaskCompleted`/`TaskCancelled` notification flow | **FIXED** — `test_notifications.py` has `test_task_completed_notifies_creator`, `test_task_completed_notifies_both_creator_and_assignee`, `test_task_cancelled_notifies_creator`, `test_task_completed_deduplicates_when_creator_is_assignee`. |
| TST-3 (P1) | Objects KPI only tested on empty state | **FIXED** — `test_director_dashboard.py` now covers `IN_PROGRESS` counted, `COMPLETED` excluded, overdue with/without `planned_end_date`, top-5 cap. |
| **COR-1 (P1)** | FTS + cursor pagination incompatible | **STILL OPEN** — no `q + cursor` guard added; see finding #1 below (unchanged repro). |
| **COR-3/REL-3/API-2 (P1)** | Cursor `anchor=None` silently returns page 1 (contracts) | **PARTIALLY FIXED** — tasks (`task_queries.py:51-52`) now returns empty page on undecodable/foreign cursor. **Contracts unfixed** — see finding #2 below. |
| **COR-2 (P1)** | Post-write reads hit replica (rule 4.13) | **STILL OPEN** — see finding #3 below; pattern is now replicated into `create_task`/`update_task`/`change_task_status` in the new Tasks domain too. |
| REL-2 (P1) | Cursor anchor lookup bypasses replica routing | **Not a real bug** — `EnterpriseClusterRouter.db_for_read` routes per-model at the DB-router layer regardless of whether the call goes through `ContractRepository.get_queryset()` or `Contract.objects` directly; there is no code path that "bypasses" routing. Downgrading — no finding raised. |
| MNT-3 (P3) | `TaskAttachment.Meta` empty, ORM blind to partial unique index | **STILL OPEN**, low severity — no `UniqueConstraint` added to model; DB-level index is correct via raw SQL migration, but `makemigrations` remains unaware of it. |

---

### P0 — Critical

| # | File | Issue | Reviewer | Confidence |
|---|------|-------|----------|------------|
| 1 | `backend/apps/requests/migrations/0002_add_resource_types.py:11` | Live migration-linter run fails the merge gate | data-migration | 100 |

- **#1** — Running `python3 infra/scripts/migration_linter.py` against this branch **exits 1**: `QUALITY GATE FAILED: backend/apps/requests/migrations/0002_add_resource_types.py -> Прямое изменение типа поля запрещено`. The migration uses `AlterField` to add two new choices (`SEWAGE`, `CONSTRUCTION`) to `resource_type`; the project's own governance script regex-matches any `AlterField` as forbidden per the Expand & Contract policy (ADR-0002/rule cited as "ADR 0027" in the script, though no such ADR file exists in `docs/adr/` — the message references a non-existent document, a secondary maintainability problem). Whatever the semantic safety of this particular `AlterField` (choices-only, no type/precision change), **the repo's stated CI gate blocks it as written**, and this is not a hypothetical — the command was executed against current `HEAD` and failed with exit code 1. Fix: either restructure as an Expand & Contract-compliant migration (new field + data migration + drop old), or explicitly carve out a choices-only exception in `migration_linter.py` with a matching ADR, since blocking all `AlterField` unconditionally is itself overly blunt and will keep firing on benign changes.

---

### P1 — High

| # | File | Issue | Reviewer | Confidence |
|---|------|-------|----------|------------|
| 2 | `backend/apps/requests/services/request_workflow_service.py:21,28,42-46,54` | Mutations bypass tenant-scoped filter and `EnterpriseSreRepository` entirely | correctness, project-standards | 100 |
| 3 | `backend/apps/contracts/queries/contract_queries.py:74-78` | Cursor `anchor=None` still silently restarts pagination at page 1 | correctness | 100 |
| 4 | `backend/apps/tasks/api.py:82,121,142` | Post-write read hits replica within the same request — replica-lag race reintroduced in new Tasks domain | reliability, correctness | 75 |
| 5 | `backend/apps/site/api/public_api.py:6` | `site` domain imports `RequestCreateService` directly from `apps.requests` — new cross-domain service import (Закон 1) | project-standards | 100 |
| 6 | `backend/apps/requests/services/request_workflow_service.py` (all transitions) | No `is_legal_hold` check before any state-transition mutation | correctness, security | 75 |

- **#2** — `assign()`, `start_processing()`, `convert()`, `close()` all call `Request.objects.filter(id=request_id).update(...)` with **no `tenant_id` in the filter** and **no `EnterpriseSreRepository.atomic_cas_update()`** call. AGENTS.md rule 4.6-4.7 is explicit: *"Мутации через `.filter(id=..., tenant_id=...)`, не `.get()` + `.save()`"* — this code does neither the tenant-scoped filter nor goes through the repository CAS layer, it's a raw ORM `.update()`. Practical exploitability is bounded by request IDs being non-guessable UUIDv4 and the preceding `RequestRepository.get_by_id()` read already tenant-scoping the object (so a genuine cross-tenant write requires colliding on a 122-bit random ID), but the pattern is a direct, quotable violation of the written mutation rule, has zero optimistic-concurrency protection (two concurrent `assign()` calls on the same request silently last-write-wins with no `version` bump), and provides no audit trail via the repository layer. Fix: route through `EnterpriseSreRepository.atomic_cas_update()` (or at minimum add `tenant_id=get_tenant_id()` to every `.filter()` call in this service) and increment `version`.
- **#3** — `contract_queries.py:74`: `anchor = Contract.objects.filter(id=cursor, tenant_id=ctx.tenant_id).values(...).first()` then `if anchor: qs = qs.filter(...)` — when `anchor` is `None` (deleted record between pages, or a cursor from another tenant), the boundary filter is simply skipped and the query silently returns page 1 again, producing client-visible duplicate rows. This is the exact `COR-3` scenario from the prior review, fixed in the new `tasks/queries/task_queries.py:51-52` (`if anchor is None: return TaskListOut(items=[], next_cursor=None)`) but the fix was never backported to `contracts`, which was the originally-flagged file. Fix: mirror the tasks fix — `if cursor and anchor is None: return {"results": [], "next_cursor": None, "count": 0}`.
- **#4** — `create_task`, `update_task`, `change_task_status` in `tasks/api.py` all call `get_task_detail()` (a query-side, replica-routed read per rule 4.12) **after** the enclosing `transaction.atomic()` block in `TaskService.create/update/change_status` has already closed. `EnterpriseClusterRouter.db_for_read` only forces primary when `in_atomic_block` is true or `ReadYourOwnWritesStickyMiddleware`'s Redis sticky key is already set from a *prior* request — but that sticky key is written only *after* the current response returns (`read_your_own_writes.py:36-39`), so the very first mutate-then-read-in-the-same-request cycle for a tenant is unprotected. `docs/solutions/test-failures/transaction-tests-read-stale-from-streaming-replica.md` documents this exact class of bug and states the rule plainly: *"Командная сторона (services/, repositories/, tasks/) читает только с primary; реплика — исключительно для queries/"* — `get_task_detail` is query-side and is being called synchronously right after a command-side write, reproducing the documented race in a new domain. Under replica lag, `POST /tasks/` can return a 201 body reflecting a state the replica hasn't caught up to yet (e.g., missing `assigned_to_id` immediately after assignment). Fix: call `set_force_primary_db(True)` before the post-write read in the three affected endpoints, or return the in-memory `task` object mapped through `_task_to_out()` instead of re-querying.
- **#5** — `apps/site/api/public_api.py:6` imports `from apps.requests.services.request_create_service import RequestCreateService` and calls it directly. ADR-0005 Закон 1: *"Прямой импорт... чужого домена категорически запрещён"*; Закон 2 requires Internal SDK via `typing.Protocol` in `apps/common/contracts/` for synchronous cross-domain calls. Every other cross-domain read/write in this diff (contragents -> files, contracts -> finance/files) goes through a `*Protocol` + `default_*_service` pattern; this is the one new call site in the diff that imports a concrete domain service class directly. It happens to be low-risk today (both are read/write DTOs, no shared transaction), but it's a new, avoidable regression against the project's own hard rule and the AST scan below confirms it's the only genuinely *new* violation introduced by this branch (the `contracts`/`contragents` -> `files`/`finance` imports predate this branch). Fix: add a `LeadIntakeProtocol` in `apps/common/contracts/` with a `submit_lead(...)` method, implement it in `requests`, inject into `site`.
- **#6** — None of `assign/start_processing/convert/close` check `req.is_legal_hold` before mutating. AGENTS.md: *"Перед любой мутацией — проверка `is_legal_hold`"* — `Request` inherits `is_legal_hold` from `BaseEnterpriseModel` but the field is never consulted in this service. Impact is currently bounded (Requests domain has no legal/compliance workflow attached yet), but the rule is unconditional and the gap is mechanical to close.

---

### P2 — Moderate

| # | File | Issue | Reviewer | Confidence |
|---|------|-------|----------|------------|
| 7 | `backend/apps/reports/pdf_service.py:16-17` | Self-declared "ADR-0005 exception" not backed by any actual ADR text | project-standards | 100 |
| 8 | `backend/apps/tasks/models/task_attachment.py:12-13` | `Meta` still has no `UniqueConstraint` mirroring the raw-SQL partial index | maintainability | 75 |
| 9 | `backend/apps/notifications/handlers/task_handlers.py:63-72,85-94` | `get_recipient_ids` byte-for-byte duplicated across `TaskCompletedHandler`/`TaskCancelledHandler` (carried over from prior review's MNT-4, still unresolved) | maintainability | 75 |
| 10 | `backend/apps/requests/repositories/request_repo.py:24` | `request.save()` direct model save in `create()`, bypassing repository CAS pattern (new code, same class of issue as #2) | project-standards | 75 |

- **#7** — `pdf_service.py`, `contract_report_query.py`, `finance_report_query.py`, `payments_report_query.py` all carry a docstring claiming *"ADR-0005 exception: apps/reports/ is the sanctioned cross-domain read layer"*. `docs/adr/0005-bounded-context-ownership.md` contains no such exception — Закон 1 is stated unconditionally. The query files mitigate the actual DB risk by using raw SQL (`django.db.connection`) rather than ORM model imports, which is a reasonable pattern for a report aggregation layer, but `pdf_service.py` imports `Contract` and `ContractRepository` directly (real ORM model + repository import from `apps.contracts`), which is exactly what Закон 1 forbids, self-authorized via comment rather than governance. Fix: either write an ADR amendment formally sanctioning `apps/reports/` as a cross-domain read layer (with the same reasoning already in the docstrings), or convert `pdf_service.py` to the same raw-SQL pattern the query files already use.
- **#8** — Model-level `class Meta` for `TaskAttachment` still only sets `db_table`; the tenant-scoped partial unique index added via raw-SQL migration in `0004` has no corresponding `UniqueConstraint(condition=Q(is_deleted=False), fields=[...])` declaration. A future `makemigrations` run won't detect drift if someone edits the index by hand, and Django's own migration-state tracking is blind to this constraint. Low risk since the DB-level enforcement is correct, but it's the exact residual noted in the prior review (`MNT-3`) and remains unaddressed.
- **#9** — `TaskCompletedHandler.get_recipient_ids` and `TaskCancelledHandler.get_recipient_ids` (lines 63-72 and 85-94) are identical: iterate `("creator_id", "assigned_to_id")`, parse UUIDs defensively, dedupe via `dict.fromkeys`. Same finding as prior review's `MNT-4`, not addressed in the intervening commits. Extract to a shared `TaskParticipantsMixin.get_recipient_ids()` or a module-level helper both handlers call.
- **#10** — `RequestRepository.create()` sets `tenant_id` then calls `request.save()` directly rather than going through `EnterpriseSreRepository`. For a brand-new row this is lower risk than the update-path issue in finding #2 (no existing state to race against), but it's the same category of rule violation and sits in a domain that otherwise has no repository-mediated create path at all.

---

### P3 — Low

| # | File | Issue | Reviewer | Confidence |
|---|------|-------|----------|------------|
| 11 | `infra/scripts/migration_linter.py:43` | Error message cites "ADR 0027" — no such file exists in `docs/adr/` | project-standards | 100 |
| 12 | `infra/scripts/ddd_boundary_linter.py:21` | Script crashes (`AttributeError: module 'os' has no attribute 'relpath'`) in this shell's Python — pre-existing, unrelated to this diff | project-standards | 50 |
| 13 | `frontend/src/shared/ui/cms/*.tsx`, `frontend/src/shared/ui/map/*.tsx` | Zero Vitest unit tests for 9 new CMS components and 5 new Map components (1346 lines) | testing | 75 |

- **#11** — Cosmetic but user-facing: the migration linter's own failure message (finding #1) tells developers to consult "ADR 0027," which does not exist (`docs/adr/` tops out at `0023`). Whoever hits this CI failure will search for a document that isn't there. Update the message to reference the actual governing doc (ADR-0002 zero-downtime migration governance) or write ADR-0027 if one was intended and never landed.
- **#12** — Confirmed unrelated to this diff (file untouched since `6036302`, predates the branch's base). Flagged as residual risk only: in this execution environment the DDD boundary linter cannot run at all (crashes before producing output), so the manual AST scan performed for this review (finding #5, and the confirmation that finding #2's peers are pre-existing) is the only DDD-boundary signal currently available — the repo's own automated gate is silently non-functional here. Worth confirming the Docker container's Python version differs from this host's, since `os.path.relpath` has existed in every Python 3.x release; if the container also fails this way, the CI gate for domain-isolation is not actually enforcing anything.
- **#13** — `vitest` is configured (`frontend/package.json`) but zero `*.test.*`/`*.spec.*` files exist anywhere under `frontend/src`. The 9 new CMS components (gallery upload/manage/reorder, revision history, meta editor, SERP preview) and 5 new Map components (search bar, fly-to, cadastral highlight, marker) ship with no unit coverage; only one Playwright e2e spec exists repo-wide (`files-upload-flow.spec.ts`), unrelated to these features. This is a project-wide pattern (not new to this diff), but the diff is exactly where new frontend logic worth unit-testing was added — drag/drop file filtering, upload state, fly-to bounds vs. point branching.

---

### Requirements Completeness (docs/plans/2026-06-23-001-feat-sprint-c-operational-maturity-plan.md, `plan_source: inferred`)

| Req | Description | Status |
|-----|-------------|--------|
| R1 | `kl_to_1c.txt` parses correctly, balances/status correct | Met — `test_kl_to_1c_real_file.py` covers balance parsing, direction detection, Decimal (not float) amounts |
| R2 | 4 test users w/ roles + documented RBAC matrix | Met — `create_test_users.py` gated on `settings.DEBUG` (improves on prior review's `SEC-4` env-var gate finding — now uses the stricter `DEBUG` flag directly) |
| R3 | `POST /tasks/` creates + assigns + accepts `due_date`/`entity_type+entity_id` | Met |
| R4 | DONE status change notifies creator + assigned_to | Met, tested (dedup on creator==assignee verified) |
| R5 | `GET /dashboard/director/` returns 5 KPIs, director-only | Met — RBAC-gated via `(read, director_dashboard)`, seeded in `0026`/`0027` |
| R6 | Overdue = `IN_PROGRESS AND planned_end_date < today` | Met, tested (`test_overdue_object_counted`, `test_in_progress_without_end_date_not_overdue`) |

Since this plan was located via inferred discovery (branch-name keyword match, not an explicit `plan:` argument or PR body link), unmet items would route to advisory/P3 per synthesis rules — but all 6 tracked requirements are met, so this section is informational only and does not affect the verdict.

---

### Pre-existing Issues

| # | File | Issue | Reviewer |
|---|------|-------|----------|
| 1 | `backend/apps/payments/models/bank_statement_line.py:45` | `amount` stored as `DecimalField`, not integer kopecks per AGENTS.md rule 4.6 | project-standards |
| 2 | `backend/apps/contracts/api.py:34-35`, `backend/apps/contragents/api.py` | Direct import of `default_file_service`/`default_finance_reader` singletons from concrete domain modules rather than only the `Protocol` type | project-standards |
| 3 | `infra/scripts/ddd_boundary_linter.py` | Crashes before scanning (see finding #12) | project-standards |

None of these count toward the verdict — they predate this branch's base commit (`10c4400`) and the diff does not modify or newly interact with them in a way that makes them "secondary" findings.

---

### Learnings & Past Solutions

- [Known Pattern] `docs/solutions/test-failures/transaction-tests-read-stale-from-streaming-replica.md` — directly explains and predicts finding #4 (Tasks post-write replica read). The rule stated there ("command-side reads only from primary; replica is exclusively for queries/") was not applied when wiring `tasks/api.py`'s post-mutation `get_task_detail()` calls.
- [Known Pattern] `docs/solutions/database-issues/drop-index-concurrently-reverse-sql-transaction.md` — the pattern this doc describes (direct-cursor `DROP INDEX CONCURRENTLY` in a migration's reverse path, bypassing `schema_editor.execute()`'s implicit transaction) is correctly applied in `0004_taskattachment_fix_unique_idx_tenant.py`'s `_reverse_index()`.
- [Known Pattern] `docs/solutions/patterns/cursor-pagination.md` and `docs/solutions/security-issues/rbac-enforcement-mode-silent-bypass.md` — both directly relevant; the RBAC one is fully resolved (verified above), the cursor-pagination anchor=None guidance was applied to `tasks` but not backported to `contracts` (finding #3).
- No solution doc yet exists for the `EnterpriseSreRepository`-bypass pattern seen in findings #2 and #10 (raw `.update()`/`.save()` outside the repository layer in new domains) — worth capturing via `/ce-compound` given it recurred twice in this branch alone (requests domain, both create and workflow paths).

---

### Agent-Native Gaps

Not applicable — this diff is ERP domain/CQRS backend + CMS/dashboard frontend work with no agent-tool or LLM-integration surface in scope. No findings.

---

### Deployment Notes

**Pre-deploy (blocking):**
- Resolve finding #1 (migration linter failure) before merge — this is a hard CI gate per the repo's own tooling, verified by direct execution.
- Confirm whether `infra/scripts/ddd_boundary_linter.py` runs successfully inside the actual CI/Docker environment (finding #12) — if it also crashes there, domain-isolation is currently unenforced project-wide, a governance gap independent of this branch.

**Post-deploy verification:**
```sql
-- Confirm requests_requests.resource_type accepted the new choices without truncation
SELECT resource_type, COUNT(*) FROM requests_requests GROUP BY resource_type;

-- Confirm tasks_attachments_active_unique now includes tenant_id (LRN-2 fix)
SELECT indexdef FROM pg_indexes WHERE indexname = 'tasks_attachments_active_unique';
-- Expected: ON tasks_attachments (task_id, file_id, tenant_id) WHERE is_deleted = false
```

**Rollback:** All migrations in this diff use `RunPython`/`RunSQL` with reverse operations defined; no destructive drops. Standard rollback (previous commit + `migrate <app> <previous_migration>`) applies.

---

### Coverage

- Persona dispatch: 5 parallel `use_subagents` waves attempted, all 5 failed with HTTP 402 (insufficient OpenRouter credits) before producing any output — 0 tokens of sub-agent analysis obtained. Full review conducted sequentially by the orchestrating agent instead, applying each of the 12 selected persona lenses directly.
- Live tool execution performed as part of this review (not just static reading): `python3 infra/scripts/migration_linter.py` (failed, confirms finding #1), `python3 infra/scripts/ddd_boundary_linter.py` (crashed, finding #12), a custom AST cross-domain-import scanner across all of `backend/apps/` (confirms finding #5 is the sole new violation; 47 other matches are pre-existing test-fixture imports or the pre-existing `contracts`/`contragents` -> `files`/`finance` singleton-import pattern).
- Suppressed: no findings were suppressed below anchor 75 — every reported item was independently confirmed either by direct code reading against a specific cited rule, or by executing the project's own governance tooling.
- Residual risks: replica-lag race (#4) has no test coverage in this branch and would require a live-replica integration test to catch (unit tests can't reproduce actual streaming lag); the `ddd_boundary_linter.py` crash (#12) may mean domain-isolation review for this PR (and possibly others) has never actually run in CI.
- Testing gaps: frontend CMS/Map components (#13); no test exercises `assign()`/`close()`/`convert()` under `is_legal_hold=True` for the Requests domain (would have caught #6 mechanically).
- No findings were dropped for touching protected artifacts (`docs/brainstorms/`, `docs/plans/`, `docs/solutions/`) — none of the findings above concern those paths.

---

## Actionable Findings

| # | File | Issue | Route | Notes |
|---|------|-------|-------|-------|
| 1 | `backend/apps/requests/migrations/0002_add_resource_types.py:11` | Migration linter blocks merge (`AlterField` forbidden) | `manual -> downstream-resolver` | Needs a design decision: restructure migration vs. carve out linter exception + ADR |
| 2 | `backend/apps/requests/services/request_workflow_service.py:21,28,42-46,54` | Mutations bypass tenant filter + repository CAS | `gated_auto -> downstream-resolver` | Concrete fix: route through `EnterpriseSreRepository.atomic_cas_update()` |
| 3 | `backend/apps/contracts/queries/contract_queries.py:74-78` | Cursor `anchor=None` still returns page 1 silently | `gated_auto -> downstream-resolver` | Mirror the `tasks/queries/task_queries.py:51-52` fix |
| 4 | `backend/apps/tasks/api.py:82,121,142` | Post-write read hits replica, same-request race | `gated_auto -> downstream-resolver` | `set_force_primary_db(True)` before `get_task_detail()`, or return in-memory object |
| 5 | `backend/apps/site/api/public_api.py:6` | New cross-domain service import (`site` -> `requests`) | `manual -> downstream-resolver` | Needs a `LeadIntakeProtocol` in `apps/common/contracts/` |
| 6 | `backend/apps/requests/services/request_workflow_service.py` | No `is_legal_hold` guard before mutations | `gated_auto -> downstream-resolver` | Add the check to all 4 transition methods |
| 7 | `backend/apps/reports/pdf_service.py:16-17` | Undocumented "ADR-0005 exception" claim | `manual -> downstream-resolver` | Write ADR amendment or switch to raw-SQL pattern like sibling query files |
| 10 | `backend/apps/requests/repositories/request_repo.py:24` | Direct `.save()` bypassing repository CAS on create | `gated_auto -> downstream-resolver` | Route through `EnterpriseSreRepository` |

---

## Coverage summary (Actionable Findings excluded above appear only in severity tables): #8, #9, #11, #12, #13 are lower-severity/residual items already detailed in the P2/P3 tables and Coverage section.

---

> **Verdict:** Not ready
>
> **Reasoning:** Finding #1 is a hard, currently-failing CI gate — the repo's own `migration_linter.py` returns exit code 1 against `HEAD` as-is; this alone blocks merge regardless of severity classification elsewhere. Independently, two of the three P1 correctness issues from the prior 2026-06-24 review (`COR-3`/cursor-anchor and `COR-2`/replica-race) remain unresolved and the replica-race pattern was newly reproduced in the Tasks domain rather than avoided; a new cross-domain import (finding #5) and a repository-bypass mutation pattern (findings #2, #6, #10) were introduced in the new Requests domain. All 6 tracked plan requirements (R1-R6) are functionally met and well-tested, and the majority of the prior review's findings (11 of 15 P0-P3 items) are genuinely fixed — this is a materially improved branch, not a regression-heavy one — but the still-open items are concrete, verified, and one is a mechanical merge-blocker.
>
> **Fix order:** #1 (unblock CI) -> #2/#6/#10 (Requests domain repository-pattern compliance, same fix shape, do together) -> #3 (contracts cursor parity with tasks) -> #4 (replica-race, three call sites) -> #5 (Protocol for site->requests) -> P2/P3 at reviewer discretion.
