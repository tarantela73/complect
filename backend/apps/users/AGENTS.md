# Identity and IAM instructions

Read the [repository map](../../../AGENTS.md) and [backend instructions](../../AGENTS.md) first. This guidance applies by impact to Identity/IAM/RBAC, tenant authorities, audit Legal Hold, merge/reconciliation and retirement, including callers in common infrastructure, frontend and gateway configuration.

Start with [ADR-0024](../../../docs/adr/0024-global-identity-tenant-membership.md) and the [active IAM plan](../../../docs/superpowers/plans/2026-07-30-iam-identity.md). Read the current plan status and the relevant release gates before acting; future-plan models and rollout steps are not proof of current implementation or deployment authorization.

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

Use the canonical lock order defined in [ADR-0024 — Identity merge locking and runtime enablement](../../../docs/adr/0024-global-identity-tenant-membership.md#identity-merge-locking-and-runtime-enablement) and the active IAM plan. Preserve the explicit table ranks and primary-key ordering within each rank, including UUID-ordered tenant scopes. Use the shared coordinator and check its coverage against those sources when extending the graph; a partial implementation does not redefine the canonical order.

Discover first, lock in that order, re-read the graph and retry on a changed graph. A worker must not take a target/security `SKIP LOCKED` lock before the global Identity graph locks. New graph members, lock ranks or hold-bearing tables require an ADR/plan-consistent update to the coordinator and real concurrency tests.

## Audit fence and release gates

For hold discovery, audit/session linkage, authority transitions or fence migrations, read the normative [Audit Legal Hold identity fence](../../../docs/adr/0024-global-identity-tenant-membership.md#audit-legal-hold-identity-fence-p0-2026-09-10). Protected graph commands require `READ COMMITTED` and a fresh separate hold query after complete graph fences, covering direct and session-only audit evidence before CAS. Do not add audit history to convergence row-lock ranks or replace the supported writer's dedicated lock protocol with a generic pre-check. Preserve immutable session bindings and database guards.

For migration, enablement and retirement work, read ADR-0024's [migration rollout](../../../docs/adr/0024-global-identity-tenant-membership.md#migration-rollout) and the [active plan's matching release checkpoint](../../../docs/superpowers/plans/2026-07-30-iam-identity.md). Referenced future runbooks must not be treated as existing gate evidence. Preserve signed approvals, fresh coverage/gate evidence, backup/restore proof, rollback floors, drain/quiescence and trigger/production-role verification. The narrowly approved irreversible retirement exception does not relax ADR-0002 for other migrations. External approval never replaces the in-transaction retirement barrier. Do not resume old authority processors or mixed old/new nodes after the audit fence rollout.

## Verification

Use the Docker/PostgreSQL checks in the active plan for the changed release and graph entry points. Cover tenant-scope restoration, restricted-role policies, hold-versus-transition orderings, graph rediscovery/retry and audit/session bindings when affected. Verify deployed login privileges and enabled guards separately from disposable-role tests; neither a local admin connection nor read-only MCP proves production enforcement. Report missing release evidence explicitly and keep gated execution disabled.
