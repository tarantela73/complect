# Backend instructions

Read the [repository map](../AGENTS.md) first. These instructions specialize it for `backend/` and for database, worker or backend-contract changes elsewhere. Paths in code spans are repository-relative unless explicitly described as app-local.

## Repository architecture

The bounded contexts are owned by their apps. `backend/apps/common/` provides shared infrastructure and typed contracts; `backend/apps/users/` owns Identity, IAM, MFA, sessions and RBAC; the remaining business contexts include contracts, contragents, finance, objects/GIS, files, realtime, integration, notifications, payments, reports, requests, site, map and tasks.

Keep business logic out of Django models, signals and API/controllers. The normal command path is:

```text
validated DTO/input -> domain service -> short transaction -> state change + outbox/domain event
```

Read paths belong in repositories, queries/selectors and DTO projections; do not smuggle writes into a query path. Cross-context collaboration uses a Protocol in `backend/apps/common/contracts/` or a Transactional Outbox event. Direct imports of another domain's models or services are forbidden. Before creating a new interface, establish the owning context and its public contract.

## Backend and domain boundaries

- Within each app, use `schemas/` for input/output DTOs, `services/` for commands, `repositories/` and `queries/` for persistence/read isolation, and `api.py` only for transport concerns.
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
See [CELERY QUEUE TOPOLOGY](../docs/Project_architecture.md#415-celery-queue-topology)
for the governing topology. Report runtime deviations in task findings and
resolve them through architecture governance before changing the canonical list.
For causal repair and observability, retain `outbox_record_id`,
`causal_root_id` and `correlation_id` semantics from the relevant causal ADRs.

## IAM and database access

For Identity/IAM/RBAC, tenant authorities, audit Legal Hold, graph locking or retirement, read [users/AGENTS.md](apps/users/AGENTS.md), even when the changed file is in `apps/common`, infrastructure or another context. Never infer global versus tenant ownership from a generic ORM convention.

Before claiming production RLS enforcement, verify the deployed login is neither `SUPERUSER` nor `BYPASSRLS`, has intended grants and exercises production policies. Temporary restricted test roles do not establish deployment evidence; report missing role decisions/evidence as an unresolved security constraint.

## Performance and verification

Use [architecture sections 10, 14 and 15](../docs/Project_architecture.md) for logging, cache/SLO and testing contracts, and [ADR-0004](../docs/adr/0004-sre-slo-governance.md) for SLO governance. Measure a representative workload before optimizing; inspect query counts/plans and P95/P99, not averages alone. Check N+1 and unbounded work. State cache ownership and invalidation triggers when changing caching. Preserve structured logs and correlation fields; do not add arbitrary SLOs or alert thresholds.

Choose relevant lint/type/test targets from [Makefile](../Makefile) and [workstations.md](../docs/development/workstations.md); run development and tests through Docker from the repository root. Expand verification when contracts change, regression risk is material, or a focused check fails. For tenant/RLS changes use a non-superuser, non-BYPASSRLS role; exercise migration/security concurrency with real PostgreSQL/PostGIS transactions and bounded timeouts. Unit tests alone do not prove database behavior. API schema changes require `make generate-sdk` and inspection of the generated diff. Run `git diff --check`.
