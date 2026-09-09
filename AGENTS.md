# Complect — instructions for coding agents

## Purpose and scope

This is the canonical repository instruction file for ERP «Комплект». It governs work in this repository; it is an operational guide, not a substitute for an accepted architecture decision or a task specification.

Complect is an event-driven modular monolith:

- Backend: Django 5.2, django-ninja and Granian (ASGI only), Python 3.12.
- Frontend: Next.js 15, React 19, TypeScript, Tailwind, TanStack Query and Zustand.
- Persistence: PostgreSQL 16 + PostGIS, Redis and Celery.
- API: `/api/v1/`; the Ninja OpenAPI schema produces the TypeScript SDK.

Useful entry points are [the architecture specification](docs/Project_architecture.md), [the ADR register](docs/adr/), [the domain glossary](CONCEPTS.md), `docs/solutions/`, `Makefile`, and `docker-compose.dev.yml`.

## Instruction hierarchy

Apply sources in this order, while treating a conflict with an accepted ADR as a finding rather than silently overriding it:

1. Explicit user or task instruction defines the requested outcome and scope. It changes an accepted architectural decision only when it explicitly asks for that decision to change.
2. An Accepted/Approved ADR is the source of truth for the decision it covers. The architecture specification is authoritative where no accepted ADR is more specific.
3. An approved, active implementation plan defines the rollout of its own accepted decision. It cannot weaken an ADR outside that scope.
4. This `AGENTS.md` supplies repository-wide operating rules and pointers.
5. Domain documentation and verified solution notes provide terminology and established patterns.
6. Applicable skill guidance governs the method used for a task.

When two sources disagree, identify the exact source and rule, inspect current code and history, and report or resolve the conflict before changing behavior. Do not make this guide, a plan, or a local convenience file silently override an Accepted ADR.

## Repository architecture

The bounded contexts are owned by their apps. `apps/common/` provides shared infrastructure and typed contracts; `apps/users/` owns Identity, IAM, MFA, sessions and RBAC; the remaining business contexts include contracts, contragents, finance, objects/GIS, files, realtime, integration, notifications, payments, reports, requests, site, map and tasks.

Keep business logic out of Django models, signals and API/controllers. The normal command path is:

```text
validated DTO/input -> domain service -> short transaction -> state change + outbox/domain event
```

Read paths belong in repositories, queries/selectors and DTO projections; do not smuggle writes into a query path. Cross-context collaboration uses a Protocol in `apps/common/contracts/` or a Transactional Outbox event. Direct imports of another domain's models or services are forbidden. Before creating a new interface, establish the owning context and its public contract.

## Mandatory agent workflow

1. Inspect `git status --short` first. Preserve unrelated, staged and uncommitted work; do not reset, checkout or reformat it as collateral.
2. For repository-wide analysis, substantial architecture or cross-domain work, IAM/security work, and substantial review, query CodeGraph first. Determine the dependency and impact surface before targeted reads or edits. If the repository is not indexed, state that fact and use focused file inspection instead.
3. Read the applicable ADR, active plan and domain terms before implementing. For IAM, start with ADR-0024 and `docs/superpowers/plans/2026-07-30-iam-identity.md`.
4. Make the smallest coherent change in the owning layer. Keep generated code, unrelated worktree changes and external configuration out of scope.
5. Verify in proportion to risk: lint/type checks and focused tests for code; migration, RLS and concurrency evidence for data/security changes; SDK and browser checks when their contracts or UI change.
6. Before completion, inspect the resulting diff for contradictory rules, stale references, secrets and unintentional scope expansion.

## Backend and domain boundaries

- Use `schemas/` for input/output DTOs, `services/` for commands, `repositories/` and `queries/` for persistence/read isolation, and `api.py` only for transport concerns.
- A `DomainEvent` is immutable (`@dataclass(slots=True, frozen=True)`) and carries `event_id`, `event_version`, `occurred_at` and `correlation_id`.
- Store money as integer kopecks (`BigIntegerField`), never float or a database decimal representation of rubles.
- Use cursor pagination (`WHERE id < cursor ORDER BY id DESC`) for audit, finance, notifications, websocket events and chats. Do not introduce OFFSET pagination there.
- `/api/v1/` is a frozen OpenAPI contract. A breaking change needs an ADR and a new major API version; generated files under `frontend/src/shared/api/generated/` are changed only with `make generate-sdk`.

## Persistence and repository rules

Tenant-owned aggregate writes use `EnterpriseSreRepository.atomic_cas_update()` with the expected version and a tenant-scoped predicate. Do not call model `.save()` or `.delete()` from command-side business code, and do not use a raw broad `.update()` to bypass CAS, Legal Hold or tenant checks. Soft deletion means an atomic transition to `is_deleted=True` and `deleted_at=now()`.

Global IAM security models are a deliberate exception to the tenant-owned repository shape: they have no fabricated `tenant_id`. Use their focused, versioned IAM repositories and their documented CAS predicates instead. The exception never authorizes direct ORM mutation or a dummy tenant value.

Read and write predicates must match the aggregate's ownership boundary. Index predicates, joins and foreign-key columns according to the actual access pattern; do not infer safe production performance from a small test database.

## PostgreSQL and transaction rules

For every PostgreSQL, RLS, migration, index, locking or concurrency task, load and apply `supabase-postgres-best-practices` before editing. Unit tests alone are not a sufficient database review.

- Keep transactions short and keep network, filesystem and other external work outside row-lock windows.
- Define the transaction boundary before writing a state change. Check statement/lock timeouts, query plans where material, indexes, rollback and recovery behavior.
- Acquire multiple rows in one deterministic order before modifying them; use `SKIP LOCKED` only where the documented worker design permits it.
- Verify a migration against production-like roles, data volume and locking; inspect RLS policy behavior as well as application filters.

## Tenant isolation and RLS

Tenant isolation is a security boundary. Protected API tenant/user identity comes from the verified gateway/JWT context, never from request body or query parameters.

Distinguish global IAM data from tenant-owned data. ADR-0024 makes `User` as Identity and global security records tenant-free; it makes `users_tenant_memberships`, `users_tenant_authorization_state` and `users_tenant_session_grants` tenant-scoped. Do not apply a blanket "every QuerySet has tenant_id" rule to a documented global model, and do not use its existence to create an unscoped route into tenant data.

The three IAM tenant tables use FORCE RLS. Enter them through `tenant_rls_scope(tenant_id, using=...)`, which synchronizes the ContextVar and the transaction-local `app.current_tenant_id` GUC. IAM discovery must enumerate tenants through the durable `IamTenantRegistryEntry` contract and enter each tenant scope; it must not obtain an unscoped manager over FORCE-RLS tables.

Do not claim production-like RLS enforcement until the deployed database login role is verified to be neither `SUPERUSER` nor `BYPASSRLS`, has the intended grants, and is exercised against the production policies. Tests using a temporary restricted role do not prove the deployed application role. If the deployment role design lacks an accepted decision or verification evidence, report it as an unresolved security constraint in the task findings.

## IAM, Identity and RBAC

Read ADR-0024 and the active IAM plan before changing this area. `users.User` is evolving as the global Identity (stable `identity_id` UUID); membership, tenant-local status and authorization state are separate security-sensitive authorities. A browser `IdentitySession` has one active tenant grant, and protected tenant context follows the validated grant/JWT path.

Treat identity lifecycle, membership, authorization state, session grants, MFA/recovery state, refresh rotation and reconciliation as one security graph. `RoleBinding` remains a compatibility authority until the future membership role model is introduced. Its `user_id` is the deterministic tenant-local actor UUID derived from `TenantMembership.legacy_actor_user_id`, not the canonical `identity_id` or a raw Django integer PK. Preserve that immutable actor mapping through identity merges; never rebind existing roles to the canonical Identity UUID. Do not invent tables or assume later-plan authorities already exist.

RBAC modes are `OFF`, `LOG_ONLY` and `ENFORCE`; production uses `ENFORCE`. `is_superuser=True` does not populate frontend permissions: the login contract receives them from active role bindings.

## Legal Hold and destructive operations

Legal Hold blocks soft delete, hard delete, authority mutation and retention cleanup. A check made before acquiring the aggregate graph is not protection against a concurrent hold.

For IAM authority or destructive work, use the canonical graph coordinator: discover the complete graph, acquire its prescribed locks, collect the fresh hold snapshot and perform the guarded CAS in the same transaction. Re-check the hold at each authority transition. A held merge writes only separate, append-only hold evidence; releasing a hold never resumes convergence, finalization or retention automatically.

`IdentitySecurityAuditRecord` is a global append-only security record: its only allowed payload mutation is the monotonic `is_legal_hold` latch. Any change to Legal Hold discovery, synchronization, authority or retention paths must trace audit records alongside the identity/session/merge graph and prove that the lock, snapshot, CAS and tests remain aligned. Never assume a pre-check of `require_no_legal_hold()` alone closes this TOCTOU risk.

For irreversible IAM retirement, the legal-hold latch and migration share the database advisory retirement barrier described by ADR-0024 and the IAM plan. External approval/gate evidence complements that in-transaction barrier; it does not replace it.

## Concurrency and canonical lock order

Use the canonical lock order defined in [ADR-0024 — Identity merge locking and runtime enablement](docs/adr/0024-global-identity-tenant-membership.md#identity-merge-locking-and-runtime-enablement) and the active IAM plan. Preserve the explicit table ranks and primary-key ordering within each rank, including UUID-ordered tenant scopes. Use the shared coordinator and check its coverage against those sources when extending the graph; a partial implementation does not redefine the canonical order.

Discover first, lock in that order, re-read the graph and retry on a changed graph. A worker must not take a target/security `SKIP LOCKED` lock before the global Identity graph locks. New graph members, lock ranks or hold-bearing tables require an ADR/plan-consistent update to the coordinator and real concurrency tests.

## Migration safety

ADR-0002 requires Expand and Contract:

1. Release N: add a nullable field or a new table without breaking old code.
2. Release N+1: backfill asynchronously in bounded batches (normally 5,000 with documented `SKIP LOCKED` use) and switch compatible reads/writes.
3. Release N+2: validate constraints online (`NOT VALID` then `VALIDATE`) and retire only proven-inactive structures.

Do not add a non-null active field without a safe rollout, remove/alter an active column, or create a production index without the required concurrent strategy. Provide a safe reverse path unless an approved irreversible migration documents its fresh gates, backup/restore evidence, legal holds and explicit `IrreversibleError` boundary. Migration code uses historical models, not current runtime imports.

Run the migration, DDD-boundary and OpenAPI-contract linters when applicable; test PostgreSQL/PostGIS in Docker, not SQLite.

## Transactional Outbox, Celery and domain events

Write an `OutboxRecord` in the same business transaction as the aggregate change. Publish/wake workers only after commit with `transaction.on_commit()`. Never send directly to Redis/Celery from inside the business transaction.

Delivery is at-least-once and consumers must deduplicate through the documented `ProcessedEvent.claim()` contract. Dispatcher batch selection is lock-before-slice with `SELECT ... FOR UPDATE SKIP LOCKED`; preserve consumer claim/ACK ownership and the repair/DLQ protocol. A change to delivery guarantees requires ADR-0010 review.

Use the queues accepted by the architecture specification (`default`,
`outbox`, `notifications`, `finance`, `files`, `retention`) and preserve the
configured low prefetch, late acknowledgement and bounded retry behavior.
See [CELERY QUEUE TOPOLOGY](docs/Project_architecture.md#415-celery-queue-topology)
for the governing topology. Report runtime deviations in task findings and
resolve them through architecture governance before changing the canonical list.
For causal repair and observability, retain `outbox_record_id`,
`causal_root_id` and `correlation_id` semantics from the relevant causal ADRs.

## Frontend architecture

Use feature-oriented Next.js modules. Keep server state in TanStack Query and small client/UI state in Zustand; do not use client storage for access tokens. API access goes through the generated SDK/client and feature hooks. Follow the frozen OpenAPI contract, invalidate appropriate query keys after mutations and verify changed visible flows in the browser when practical.

Frontend authorization consumes permissions returned by login and uses the project's permission checks. Do not infer permissions solely from Django superuser state. Maintain strict TypeScript and the `@/` path alias.

## Security

- All `/api/v1/` traffic passes through Kong: public, context-free and protected routes have distinct gateway trust requirements. Test CORS through the gateway rather than Django alone.
- PII uses `pgcrypto`/approved key management and every PII read is auditable with actor, reason, request and correlation context. Never write secrets, tokens, raw PII or unredacted production logs into code, documentation or commit messages.
- Mutating endpoints follow idempotency, authorization and tenant boundaries. Validate the authority from trusted context, not user-supplied identifiers.
- Treat retention, exports, audit repair and administrative IAM commands as destructive security paths: establish scope, Legal Hold, authorization, concurrency and recovery before running them.

## Testing and verification

All development and test commands run through Docker from the repository root. Use PostgreSQL + PostGIS; SQLite is prohibited. Relevant commands include:

```bash
make lint
make types
make test-fast
make test-all
make generate-sdk
make tsc
make test-e2e
```

Use focused pytest paths for the changed domain before broad suites. Test tenant/RLS changes under a non-superuser, non-BYPASSRLS role; test concurrent security or migration behavior with real transactions and bounded timeouts. When API schemas change, regenerate the SDK and inspect its diff. For visible frontend work, exercise the changed user flow and preserve evidence appropriate to the task. `git diff --check` is required for documentation and code diffs.

## Skills, plugins, MCP and tools

Plugins, Skills and MCP servers are separate capability layers. Inspect the
current environment before selecting them: a cached plugin is not necessarily
enabled, a filesystem skill is not necessarily visible to the current session,
and a configured MCP is not necessarily functional. Prefer the narrowest
applicable installed capability and read its actual `SKILL.md` before use.

For substantial tasks, choose the layers deliberately:

1. CodeGraph determines repository dependencies and impact first for the
   repository-wide, architecture, cross-domain, IAM/security and substantial
   review cases defined above.
2. A domain-specific Skill defines the work method after that impact is known.
3. A Plugin may provide specialized Skills, apps or tools; use its capability
   according to its manifest, provenance and current connection state.
4. An MCP server provides structural or external capabilities. Its presence
   never implies that a similarly named Skill or Plugin was used.

Availability is environment-dependent. This file defines routing policy, not a
guarantee that every optional capability is installed. Never claim that a
Plugin, Skill or MCP server was used unless it was actually available and used
in the current task; final reports list only Skills that were read/applied and
tools that were invoked.

### Complect capability routing

- **Repository and architecture analysis:** after the mandatory CodeGraph
  impact pass above, use
  `codebase-design` for module seams, public interfaces or deep-module design.
  Use the explicit `improve-codebase-architecture` workflow only when it is
  installed and requested; neither workflow relaxes bounded-context rules.
- **PostgreSQL, RLS, schema, migrations, indexes, locks and transactions:**
  follow the mandatory CodeGraph and standalone
  `supabase-postgres-best-practices` rules above. If a plugin exposes another
  Skill with the same name, prefer the standalone Complect-wide Skill for
  backend/database work; use a plugin-qualified variant only when the
  enclosing plugin workflow specifically requires it. Do not run both by default.
  If Postgres MCP is available and functionally verified, use its read-only
  schema, query or metrics tools for database inspection. Verify the selected
  connection and role; MCP inspection does not replace Docker/PostgreSQL tests
  or authorize database writes or MCP configuration changes.
- **Implementation from an approved spec or plan:** use `implement` only when
  it is installed and explicitly invoked. It remains subordinate to this
  file, Accepted ADRs, the approved plan, CodeGraph-first analysis and all
  database/security gates.
- **Behavioral changes and regression fixes:** use `tdd` when its trigger
  applies. RLS, migration and concurrency tests still run against real
  PostgreSQL/PostGIS and do not stop at unit-only evidence.
- **Hard bugs, intermittent failures, races and performance regressions:** use
  `diagnosing-bugs` to establish a reproducible feedback loop; use CodeGraph
  before broad fixes.
- **Formal diff review:** use `$code-review` only after loading its actual `SKILL.md`
  and pinning a commit, branch, tag, merge-base or explicitly accepted
  baseline. Keep its Standards axis (this file, Accepted ADRs and applicable
  security/database rules) separate from its Spec axis (issue, task or
  approved plan). When the `codex-security` plugin is available, select its
  `security-diff-scan` for a security diff review or `security-scan` for a
  scoped repository audit. Security-specific scan/review plugins are additional only
  when the task explicitly requires a security review; do not launch
  overlapping review workflows automatically.
- **Domain vocabulary and bounded contexts:** use `domain-modeling` when the
  task changes terminology, aggregates, ownership, concepts or contracts.
  Accepted ADRs remain authoritative.
- **React and Next.js:** when available, use the plugin-provided
  `react-best-practices` for rendering boundaries, performance, data fetching,
  bundles and client/server component decisions, together with Complect's
  frontend architecture.
- **shadcn/ui:** use the plugin-provided `shadcn` Skill for shadcn registries,
  components, composition, installation, styling and debugging when the
  project actually uses shadcn or the task explicitly introduces it.
- **Rendered frontend testing/debugging:** use the narrow
  `frontend-testing-debugging` Skill for UI regressions, responsive behavior,
  console errors and interaction failures. Use a functional Browser capability
  first when its Skill requires one; otherwise use the repository's Playwright
  workflow and record why.
- **External technical research:** use `research` for a primary-source
  investigation with a persisted Markdown artifact. It does not replace local
  repository analysis through CodeGraph.
- **Merge/rebase conflicts:** use `resolving-merge-conflicts`; trace
  architecture-sensitive intent through the applicable ADR/plan and
  CodeGraph before resolving a hunk.
- **Issue/spec workflows:** use `to-spec`, `to-tickets`, `triage` and
  `wayfinder` only when they are installed, explicitly invoked and the issue
  tracker in `docs/agents/issue-tracker.md` is configured. Never run
  `setup-matt-pocock-skills` automatically during ordinary coding work.
  For triage label mapping and provisioning prerequisites, read
  `docs/agents/triage-labels.md`.
- **Domain documentation:** read `CONTEXT.md` if it exists and otherwise use
  `CONCEPTS.md`, following `docs/agents/domain.md`.

Do not install, enable, disable, update, replace, remove or reconfigure a
Plugin merely because it could improve a task. Such changes require explicit
task scope or user authorization. Confirm an external app's individual
connection before using it and never infer a Codex plugin from ChatGPT-only
availability.

MCP configuration changes require explicit task scope or user authorization.
Within that authorized scope, apply the add-only policy by default: inspect
global and project config and run `codex mcp list`, then add a new server under
a unique ID. Deletion, disabling, renaming, replacement or overriding an
existing server's transport/auth/tool policy needs an explicit user request
that names the exact server ID. Reusable configuration belongs in
`~/.codex/config.toml`; Complect-only configuration belongs in
`.codex/config.toml`; never duplicate one ID across both. Reference credentials
through environment variables, functionally verify the intended read-only
capability when safe, and run `codex mcp list` again after a change.

## Documentation and ADR governance

Keep one meaning in one authoritative place. `AGENTS.md` states operational rules; ADRs hold decisions and trade-offs; implementation plans hold rollout steps; `CONCEPTS.md` holds the glossary; solution documents hold verified diagnostics. Do not copy a whole ADR or plan into this file.

Create or amend an ADR for a hard-to-reverse, surprising decision with genuine trade-offs. Update the relevant plan, contract, runbook and tests together when a decision changes. Historical documents may describe past behavior, but must not be presented as the current implementation contract.

## Definition of Done

Before declaring a task complete:

- the requested change is implemented in its owning boundary without breaking an Accepted ADR or undocumented cross-domain dependency;
- Legal Hold, tenant/RLS, IAM and concurrency implications have been assessed when relevant;
- migrations/outbox/API/frontend contracts have their required verification;
- focused tests and appropriate lint/type/schema checks have passed, or a real limitation is stated precisely;
- the final diff is clean, scoped, free of stale instruction references and does not include unrelated worktree changes;
- the final report names material decisions, changed files, verification and any explicit unresolved constraint.
