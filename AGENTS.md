# Complect — repository instructions

ERP «Комплект» is an event-driven modular monolith: Django 5.2 / django-ninja / Granian (ASGI only), Python 3.12; Next.js 15 / React 19 / TypeScript; PostgreSQL 16 + PostGIS, Redis and Celery. This file is the repository-wide map and mandatory invariants, not a replacement for architecture decisions.

## Instruction hierarchy

1. Explicit user/task instructions define outcome and scope; an accepted architectural decision changes only when explicitly requested.
2. Accepted/Approved ADRs govern their decisions; [Project_architecture.md](docs/Project_architecture.md) governs where no accepted ADR is more specific.
3. An approved active plan governs rollout of its own accepted decision and cannot weaken another ADR.
4. This root file, scoped `AGENTS.md` files and linked operational guidance govern agent work. Scoped files specialize their area; they must not weaken root invariants or accepted decisions.
5. Domain docs and verified solution notes supply terminology and established patterns.
6. Applicable Skills define the work method within these constraints.

For conflicts, identify the exact sources/rules, inspect code and history, then report or resolve before changing behavior. Keep one meaning in one authoritative place: ADRs hold decisions, plans rollout, [CONCEPTS.md](CONCEPTS.md) vocabulary, and [solutions](docs/solutions/) verified diagnostics. Create/amend an ADR for a hard-to-reverse decision with genuine trade-offs; update the relevant plan, contract, runbook and tests together. Historical docs are not current implementation contracts.

## Mandatory workflow

1. Run `git status --short` first. Preserve unrelated staged/uncommitted work; never reset, checkout or reformat it as collateral.
2. Query CodeGraph **before targeted reads or edits** for repository-wide analysis, substantial architecture/cross-domain work, IAM/security work and substantial review. Determine dependencies and impact first. If unavailable or unindexed, report that and use focused inspection; do not silently initialize or reconfigure it. For docs/configs absent from the graph, use targeted reads after the impact pass.
3. Apply root and scoped instructions, ADRs, the active plan and domain terms **by affected behavior, regardless of starting directory or edited path**. Load missing applicable instructions, including ancestors; reuse already-read unchanged instructions and load additional guidance when scope expands. Consult [domain guidance](docs/agents/domain.md), using canonical root `CONCEPTS.md`.
4. Infer intent from context and carry action requests (including “can you”, “I want” and “help me”) through to the agreed outcome. Make the smallest coherent change in the owning layer; preserve task and authorization boundaries. Keep unrelated work out of scope; generated files follow their dedicated workflow.
5. Verify in proportion to risk using Docker from the repository root: focused tests/lint/types for code; migration/RLS/concurrency evidence for data/security; SDK/browser checks for contracts/UI. PostgreSQL + PostGIS only; SQLite is prohibited. Avoid tests that merely mirror trivial reversible edits. Follow [test isolation and authorization](docs/development/workstations.md#test-isolation-and-authorization) before unattended test execution; commands are in [Makefile](Makefile).
6. Finish when the agreed change and applicable checks are complete and regressions caused by your changes are fixed, not merely after the first passing test. Report pre-existing unrelated failures separately. Inspect the final diff for conflicts, stale links, secrets and scope expansion; run `git diff --check`. Report decisions, files, completed checks and what remains unverified; a blocked check is not a pass.

## Session state

For multi-session work, task state, verification evidence and session closure, follow [the harness workflow](docs/agents/harness.md). Read `feature_list.json`, `progress.md` and `session-handoff.md` at startup; keep one active feature and require current evidence before marking it done. `./init.sh` checks the harness only; task-specific Docker/API/browser gates still apply.

## Required routing and project map

Read the linked instructions for every matching impact, including work in `docs/`, `infra/` or another domain. The root invariants below always apply.

| Affected area / behavior | Required local instructions and sources |
| --- | --- |
| Backend, persistence, PostgreSQL/RLS, migrations, locks, concurrency, workers, Outbox or backend contracts, wherever edited | [backend/AGENTS.md](backend/AGENTS.md); applicable [ADRs](docs/adr/) |
| Identity/IAM/RBAC, tenant authorities, sessions/MFA, audit Legal Hold, merge/reconciliation, graph locking or retirement; includes common auth, gateway, frontend and runbooks | [backend/apps/users/AGENTS.md](backend/apps/users/AGENTS.md), [ADR-0024](docs/adr/0024-global-identity-tenant-membership.md), [active IAM plan](docs/superpowers/plans/2026-07-30-iam-identity.md) |
| Frontend, generated client consumers, authentication/permission UI, accessibility | [frontend/AGENTS.md](frontend/AGENTS.md); architecture section 7 |
| Gateway, production roles, deployment, local infrastructure | Architecture and applicable ADRs; [workstations.md](docs/development/workstations.md); also backend/IAM routes when their boundaries are affected |
| Dependency/toolchain changes | [dependencies.md](docs/development/dependencies.md) |
| Logging, monitoring, performance/cache or testing policy | Architecture sections 10, 14, 15 and [ADR-0004](docs/adr/0004-sre-slo-governance.md); relevant scoped instructions |
| Skills, Plugins, MCP, specialist agents or issue/spec workflows | [capabilities.md](docs/agents/capabilities.md); MCP configuration policy in [workstations.md](docs/development/workstations.md#codex-and-postgresql-mcp) |

`backend/apps/common/` owns shared infrastructure and typed contracts; `backend/apps/users/` owns Identity/IAM. Other business contexts are owned by their apps (contracts, contragents, finance, objects/GIS, files, realtime, integration, notifications, payments, reports, requests, site, map, tasks). Frontend uses feature-oriented modules.

## Cross-cutting invariants

- **Ownership and commands:** no direct imports of another domain's models/services. Collaborate through Protocols in `backend/apps/common/contracts/` or Transactional Outbox events. Commands follow validated input → domain service → short transaction → state change + Outbox/domain event. Keep business logic out of models, signals and API/controllers. Reads use repositories/queries/selectors and DTO projections; never write through a read path.
- **Protected mutation:** tenant-owned aggregates use `EnterpriseSreRepository.atomic_cas_update()` with expected version and tenant-scoped predicate. No command-side `.save()`/`.delete()` or broad `.update()` bypass of CAS, Legal Hold or tenant checks. Global IAM uses its focused versioned repositories with documented CAS predicates and no dummy tenant. Read/write predicates must match ownership.
- **Outbox:** aggregate and `OutboxRecord` commit together; wake/publish only after commit via `transaction.on_commit()`, never Redis/Celery inside the business transaction. Delivery is at-least-once; consumers deduplicate through `ProcessedEvent.claim()`. Preserve claim/ACK and repair/DLQ semantics; delivery changes require ADR-0010 review.
- **Tenant and IAM boundary:** protected tenant/user authority comes from verified gateway/JWT context, never body/query identifiers. Global Identity/security records are tenant-free; memberships, tenant authorization state and session grants are tenant-owned and FORCE RLS. Enter them through `tenant_rls_scope(tenant_id, using=...)`; discovery uses durable `IamTenantRegistryEntry` inventory and per-tenant scopes, never an unscoped manager. Preserve tenant-local `RoleBinding` actor mapping through merges. Never claim production RLS proof from a temporary test role: verify deployed login is neither `SUPERUSER` nor `BYPASSRLS`, its grants and production policies; missing evidence is an unresolved security constraint.
- **Legal Hold and locks:** holds block deletion, authority mutation and retention. IAM uses the complete canonical graph coordinator, ADR-0024 table/PK/tenant order, fresh hold snapshot and guarded CAS in the same transaction; a pre-check is insufficient. Audit records remain append-only except the monotonic hold latch and participate in hold discovery. Releasing a hold never automatically resumes convergence/finalization/retention. Preserve the shared retirement barrier between hold latching and irreversible DDL, and the required release gates. Treat retention, exports, audit repair and IAM administrative commands as destructive security paths requiring scope, authorization, hold/concurrency and recovery assessment.
- **Database and migrations:** keep transactions short and external I/O outside row-lock windows. Apply `supabase-postgres-best-practices` before PostgreSQL/RLS/migration/index/locking/concurrency edits; follow capability routing for availability. ADR-0002 Expand/Contract, concurrent production indexes, historical migration models and safe rollback remain mandatory; only explicitly approved irreversible exceptions with fresh gates, hold and backup/restore evidence may depart. Real PostgreSQL/PostGIS migration, RLS and concurrency verification is required where affected; unit tests alone are insufficient.
- **API and frontend:** `/api/v1/` is a frozen OpenAPI contract; breaking changes need an ADR and new major version. All API traffic passes through Kong under the route's trust requirements; test CORS through Kong. Generate `frontend/src/shared/api/generated/` only with `make generate-sdk`; regenerate and inspect when schemas change. Access APIs through the SDK/client and feature hooks. No client storage for access tokens. Login permissions and project permission checks govern UI, not Django superuser status. Mutations respect idempotency, authorization and tenant boundaries.
- **PII and secrets:** PII uses `pgcrypto`/approved key management; every PII read is auditable with actor, reason, request and correlation context. Never put secrets, tokens, raw PII or unredacted production logs in source, docs or commit messages.

## Capability discipline

CodeGraph establishes impact first; then use the narrowest available Skill, reading its actual `SKILL.md`. Plugins, Skills and MCP servers are separate layers: cached/configured does not mean enabled or functional. Report only capabilities actually used. Specialized exploration/review/verification guidance lives in [.codex/agents](.codex/agents/), with detailed routing in [capabilities.md](docs/agents/capabilities.md).

Do not install, enable, disable, update, remove or reconfigure Plugins/MCP without explicit task scope or authorization; confirm external app connections individually. Authorized MCP changes follow add-only policy by default; replacing/disabling/renaming/deleting or overriding an existing server requires an explicit request naming its ID. Read the linked configuration policy before such changes. Local instruction files must be usable without network access; external references are not runtime instructions.

Отдавай независимую работу помощникам, когда параллельный запуск сокращает общее время или расширяет охват и при этом не создает конфликтующих правок.

Хорошо делегируется: исследование по разным направлениям, независимый разбор репозиториев, вычитка документации, анализ отдельных наборов данных.

Не делегируется: строго последовательная работа, мелочь, где согласование дороже выигрыша, одновременные правки одного и того же куска кода.
