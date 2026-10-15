# IAM-IDENTITY Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate authentication from tenant-bound `users.User` state to the ADR-0024 global Identity, mandatory MFA, IdentitySession and tenant-grant foundation without replacing `AUTH_USER_MODEL` or breaking existing domain consumers.

**Architecture:** `users.User` remains the physical Identity table and keeps its integer Django primary key. A nullable expand release introduces a stable `identity_id`, global security records and tenant memberships; reconciliation and batched backfill run before a separately deployed contract migration adds final uniqueness, non-null and foreign-key constraints. A signed collision decision first creates a durable, versioned `APPROVED_PENDING_CONVERGENCE` operation with immutable approval/identity payload and without changing login, email or membership authority. An owner-confirmed active ceremony, or a narrowly defined dormant fail-closed ceremony, establishes one canonical password/MFA authority before membership ownership and synthetic normalized email change atomically. Password verification creates only a five-minute pre-auth challenge: identities with a confirmed factor receive `MFA_PARTIAL`, while identities that must enroll receive `MFA_SETUP_PARTIAL`. Access/refresh tokens and tenant grants are created only after factor verification or setup confirmation and tenant selection. A signed current-context token gives the narrow RLS-safe switch repository the old tenant/grant coordinates, while durable operation records and post-rollback reconciliation enforce idempotency and fail-closed DB/Redis recovery.

**Tech Stack:** Django 5.2, django-ninja, PostgreSQL 16 with RLS, Redis DB 4, Celery, PyJWT, `cryptography` AES-GCM, pyotp, Next.js 15/React 19, Docker-only pytest.

**Status:** RELEASE N LOCALLY IMPLEMENTED; PRODUCTION GATE PENDING (v12.1).
Tasks 1–2 are implemented and verified against PostgreSQL in Docker. The
protected production reconciliation report, operator signatures, coverage
attestation and deployment approval remain manual release evidence; merge
execution therefore stays disabled by default. The hard release boundary is
unchanged: no Task 3 backfill, Task 4 contract migration or Tasks 5–17 auth
foundation/retirement work is included in Release N.

**Release N auth-boundary repair (2026-09-17):**
`VERIFIED_AUTH_TENANT_CONTEXT_BOUNDARY` follows the narrow
[ADR-0007 clarification](../../adr/0007-api-gateway-trust-boundary.md#уточнение-release-n-verified_auth_tenant_context_boundary-2026-09-17).
Protected HTTP context is published only after backend access-JWT verification
and the existing users validator, before tenant-consuming middleware or RBAC.
The users adapter is registered in `UsersConfig.ready()` before URL resolution;
this supersedes the earlier `config.api` registration location only. Internal
propagation remains separate from HTTP input. This repair introduces no migration
or Tasks 3–17 model/issuer/session/grant changes and closes no other P0 or
production acceptance gate.

**P0 audit Legal Hold fence rollout (2026-09-10):** the normative protocol is
in [ADR-0024, Audit Legal Hold identity fence](../../adr/0024-global-identity-tenant-membership.md#audit-legal-hold-identity-fence-p0-2026-09-10).
This checkpoint supersedes older rollback examples wherever they would permit
authority mutation without audit-hold discovery or with inactive DB guards.

- Keep canonical `0003_identity_expand` byte-for-byte unchanged. Insert the
  independently reversible `0003a_identity_audit_fence` after it. Existing
  future `0004_identity_contract_constraints` and `0005_retire_legacy_user_authority`
  keep their numbers; future `0004` depends on `0003a`. No users drift-repair
  migration was found on disk or in the inspected development migration ledger.
  Environmental schema drift must be reconciled separately before the fence
  migration; it is not repaired implicitly by this stream.
- Drain old authority/convergence, credential, requeue and cleanup processes.
  Keep them disabled until all nodes run fresh post-fence audit discovery.
  Run canonical schema and existing-held-binding preflight. Apply `0003a`
  before enabling the supported writer or relying on trigger enforcement.
  DDL lock timeout is 2s and statement timeout 30s; a timeout aborts the atomic
  migration. Retry deployment in a fresh quiescent window, never weaken guards.
- Verify PostgreSQL `sha256(bytea)`, Python/SQL positive and negative key vectors,
  invoker/search_path/volatility/owner/grants, trigger enabled state and runtime
  role restrictions. Exercise production login permissions and policies; the
  local `codex_ro` inspection role and disposable test role are not production
  application-role evidence. Existing missing/conflicting held references block
  migration and require an explicit legal/reconciliation decision.
- Deploy the repository-backed internal writer and fresh graph snapshot code
  together. Writer authorization occurs before its dedicated transaction;
  document the caller's actual policy before wiring any future entry point.
  Preserve retirement-shared -> complete ordered identity set -> ordered
  session/audit rows -> revalidated monotonic latch. No endpoint is introduced.
- Confirm both hold/convergence orderings for owner and dormant flows, held
  INSERT/latch bypass rejection, session linkage/reuse, unsupported isolation,
  bulk order, append-only semantics and restricted-role enforcement with
  `bash infra/scripts/run_iam_fence_tests.sh`. This creates a unique disposable
  PostGIS database plus isolated Redis and removes both after the run. Follow
  with `bash infra/scripts/run_iam_fence_tests.sh apps/users/tests`.
- Roll back application only to builds retaining audit discovery and fences,
  or leave all authority processors disabled. Reverse `0003a` only after
  draining these flows; canonical append-only and retirement behavior is
  restored, but that does not authorize old nodes to resume. Keep the existing
  later retirement and durable-merge rollback-floor gates as well.

**Execution checkpoint (2026-09-05):** migration `0003`, RLS/integrity
triggers, normalized-email reconciliation, signed collision decisions,
owner/dormant convergence, merge-aware credential guards, complete graph lock
ordering, legal-hold evidence, expiry/requeue workers and runtime coverage
attestation are present. Explicit-alias tenant ORM now uses a same-connection
`tenant_rls_scope`; Release N also persists a narrow merge Redis-cleanup record
and closed requeue `action_kind`/`resume_mode` without introducing
IdentitySession authority. Owner/dormant convergence now uses exact locked
Legal Hold snapshots, focused CAS repositories and same-transaction
pseudonymous audit/outbox evidence; durable PII audit metadata contains no raw
User PK. `apps/users/tests` passes (117 tests), Django reports
no pending `users` model changes, and `git diff --check` is clean. The
repository migration quality gate remains blocked only by the pre-existing,
out-of-scope `backend/apps/requests/migrations/0002_add_resource_types.py`
finding called out below. Step 7 commits are deliberately left unchecked
because this execution did not create commits.

## Global Constraints

- Follow [ADR-0024](../../adr/0024-global-identity-tenant-membership.md); `AUTH_USER_MODEL` remains `users.User`.
- Do not rewrite existing domain foreign keys to `users.User` in Plan 1.
- Global security models do not inherit `BaseEnterpriseModel`, do not use RLS and do not contain a fabricated `tenant_id`.
- Tenant-scoped IAM models use RLS and explicit `tenant_id` filtering.
- `TenantMembership` never stores a working copy of the global Identity email.
- Every membership stores an immutable `legacy_actor_user` FK to the existing
  integer-PK `users.User` row whose deterministic UUID is the tenant-local
  business actor. For an ordinary membership it is the same row as the global
  Identity; after an approved cross-tenant collision merge it may be a locked
  source tombstone. Global credentials always belong to the canonical
  Identity, while historical domain/RBAC authority remains attached to the
  membership actor.
- Collision approval and collision completion are different operations.
  Approval creates `IdentityMergeOperation(APPROVED_PENDING_CONVERGENCE)` and
  changes no membership, normalized email, password, MFA, status, session or
  token authority. `APPROVED_PENDING_CONVERGENCE` remains an unresolved email
  collision and blocks Task 4.
- `--correct-email` rejects a target participating as source or canonical
  Identity in a live `APPROVED_PENDING_CONVERGENCE` operation with
  `MERGE_OPERATION_ACTIVE`. The
  operator must first execute a separately signed `--cancel-merge`, then
  obtain a new purpose-bound signature for `--correct-email`; correction never
  performs an implicit cancellation or reuses merge approval. This is an
  additional guard, not sufficient eligibility: realized
  deferred/ready/finalized sources remain non-correctable compatibility rows.
  The correction envelope binds expected target `security_version` and the
  canonical digest of all locked participant operation IDs/states/versions;
  replaying a pre-cancel signature fails with `APPROVAL_CONTEXT_STALE`.
- `APPROVED_PENDING_CONVERGENCE -> APPROVED_DEFERRED` is the only merge point.
  For an active graph it requires a fully authenticated source owner, one new
  password hash written once to the canonical Identity and non-held source
  compatibility shadows, and proof of the canonical encrypted MFA factor. A
  dormant graph may bypass password/MFA convergence only when canonical and
  every source have `is_active=False` with status `LOCKED`,
  `PENDING_CREDENTIALS` or `MFA_SETUP_REQUIRED`, every membership is `DISABLED`
  or `DRAFT`, and all
  sessions, grants, refresh families, challenges and recovery operations are
  revoked, expired or absent. Mixed active/inactive graphs are rejected.
- A graph recorded with `convergence_mode=DORMANT_FAIL_CLOSED` cannot promote
  canonical or membership status to `ACTIVE` while deferred/ready. It must
  finalize while inactive during Task 16; only then may normal canonical
  onboarding activate it.
- From `APPROVED_DEFERRED` until the signed Task 16 cutoff, source username is
  only a compatibility selector; Task 16 removes that selector path.
  Password, MFA, security status/version, legal hold, recovery and device
  policy are resolved exclusively from the canonical Identity before any
  password comparison, Redis lookup or token acceptance. Source password/MFA
  columns are non-authoritative shadows.
- The normal invariant `normalize(User.email) == User.normalized_email` has one
  closed compatibility exception: a source row backed by one exact realized
  merge operation may retain raw email/username while its globally unique
  `normalized_email` is the reserved `@identity.invalid` value. Such a row is
  excluded from canonical Identity email/login/recovery row selection and,
  before Task 16 only, is reached through the merge-aware username alias
  resolver; audit JSON is never sufficient to activate this exception.
- The first `APPROVED_DEFERRED` row creates an explicit compatibility rollback
  floor: application rollback may not go below Task 2's merge-aware guard and
  schema rollback may not go below `0003`. Reverse migration and startup fail
  closed rather than losing the alias-to-canonical authority mapping.
- Invite schema, issue/consume/resend/revoke services and invite API belong to Plan 2 IAM-EMPLOYEES. Plan 1 creates no `InviteToken` implementation.
- Release N expand fields `identity_id` and `normalized_email` are nullable and non-unique. Do not add a per-table default UUID during expand.
- Final uniqueness is `UNIQUE(normalized_email)` and `UNIQUE(identity_id)` only after the protected reconciliation report is clean and backfill is complete.
- Migration A and Migration B must ship in separate production releases. Task 4 is a hard manual gate and must not be merged into the Release N deployment.
- No access token, refresh token, `IdentitySession` or `TenantSessionGrant` is issued after password verification alone.
- `Identity.SecurityStatus` is closed to `PENDING_CREDENTIALS`, `MFA_SETUP_REQUIRED`, `ACTIVE` and `LOCKED`; `ACTIVE` requires a confirmed factor.
- Security lifetimes are fixed: access JWT 15 minutes, refresh/`IdentitySession` 7 days, `MFA_PARTIAL` 5 minutes, `MFA_SETUP_PARTIAL` 5 minutes, `TENANT_SELECTION` 5 minutes and `RecoveryToken` 30 minutes.
- The tenant-switch response envelope remains replayable for ten minutes. An
  expired `TENANT_SELECTION` token is accepted only to replay a pre-existing
  `DB_COMMITTED` or `COMPLETED` operation created before that token expired;
  this exception can neither create a session/grant nor resume `PENDING` work.
- Exact `POST /api/v1/auth/tenant/select` is decoded by its endpoint-specific selection-token
  service before generic access-token authentication; no middleware or Gateway
  layer may discard the expired-replay candidate first.
- New passwords are 12–128 characters and pass Django `MinimumLengthValidator`, `CommonPasswordValidator` and `UserAttributeSimilarityValidator`; Plan 1 adds no composition rule.
- Every new grant supersedes any open grant for the session, including an expired open grant.
- Cross-tenant switching uses only `TenantGrantSwitchRepository`; its
  tenant-dependent operations use one parameterized cursor on `default`,
  never replica or router-backed tenant ORM. No generic unscoped manager, RLS
  disablement or all-tenant ORM API is allowed.
- Switch/refresh tokens sign `session_id`, current grant, current tenant and context version; every signed value is revalidated against locked DB rows.
- `TenantGrantSwitchOperation.operation_id` is the durable idempotency
  authority. The exact access/refresh/CSRF response is retained in a
  short-lived encrypted result envelope; replay never rotates a second token
  family.
- Every completed-envelope replay first proves on primary that the session,
  Identity security snapshot and exact result grant remain valid. Session
  revoke cryptoshreds every live switch-response envelope for that session
  only after the mandatory no-hold precheck succeeds.
- Reconciliation writes happen in a new transaction after a DB rollback or after a committed DB transition whose Redis CAS failed.
- Every unresolved `SessionReconciliationRecord` is actively processed by a
  retention-queue worker that can only revoke/clean up ambiguous state; it
  never restores access.
- Every reconciliation row has one closed source kind:
  `REFRESH_ROTATION`, `TENANT_SWITCH` or `SESSION_FAMILY_CLEANUP`.
  Only `TENANT_SWITCH` may reference a `TenantGrantSwitchOperation`; refresh
  and logout cleanup never attempt to discover or close one.
- Every `RecoveryToken` has one immutable `operation_id` OneToOne link to the
  exact `RecoveryOperation` that owns its delivery. Selector
  `recovery_id` is the token ID, not an Identity/purpose lookup key.
- Auth failures use one closed `AuthFailureCode -> HTTP policy` table. Call
  sites cannot choose an arbitrary status code; temporary 429/503 responses
  carry the policy-defined `Retry-After` header.
- Legal hold follows the project-wide invariant without an IAM exception:
  `identity_legal_hold=True` or `is_legal_hold=True` blocks every mutation,
  including status/version changes, session or grant revoke/supersede,
  cryptoshredding and retention. The command fails atomically with typed 403;
  held state remains unchanged until the hold is released through the
  separately authorized legal process.
- Merge hold observation never mutates a held aggregate. A hold on the merge
  operation, canonical Identity, source actor or linked security/tenant row is
  recorded in append-only `IdentityMergeHoldBlock`; release creates a signed
  append-only `IdentityMergeRequeue` and immutable result. Absence of a hold
  never resumes convergence/finalization automatically.
- Plan 1 creates no legal archive. Release N+3b is therefore blocked while
  any global or tenant-scoped IAM row is held; the destructive migration must
  fail before its first DDL operation and leave every legacy value intact.
  Moving held evidence to an immutable archive requires a separate ADR and
  explicit authorization.
- Public unauthenticated login and recovery-start boundaries never reveal a
  hold: their internal hold denial is normalized to the same invalid-login
  response or generic recovery `202` as an unknown Identity, with no challenge,
  token, operation or outbox mutation. Authenticated/administrative commands
  retain the typed 403.
- Access JWT compatibility keeps the tenant-local deterministic actor UUID in
  `user_id`; new IAM uses canonical `identity_id` and `sub`.
  `legacy_user_pk` temporarily carries `membership.legacy_actor_user_id`, not
  necessarily the canonical Identity PK.
- New access JWTs use the exact registered claims `iss="identity-v1"` and `aud="complect-api"`; gateway and backend validate both.
- Before permissions, backend validation proves `sub`/`identity_id` resolve
  to the canonical Identity, then proves `legacy_user_pk`/`user_id` resolve to
  the exact immutable actor selected by the claimed membership. Session,
  membership, grant and tenant claims must form that same context;
  split-brain claims fail closed.
- `/api/v1/auth/login` preserves the frozen `username` request field and adds
  optional `email`; exactly one non-empty identifier is accepted. Supplying
  both is rejected as `422 AMBIGUOUS_IDENTITY_INPUT` before any Identity
  lookup, so status/body/timing cannot probe whether either exists. Existing
  response fields remain unchanged and new flow fields are optional only.
- JWT/version rejection returns a typed `ErrorEnvelope`; `TENANT_SWITCHED` must not collapse into generic authentication failure.
- Identity issuer and validator are enabled/disabled by one feature mode. Production security-version validation never falls back to `LOG_ONLY`.
- Access tokens stay only in browser memory. The sole server-side bearer
  replay exception is Task 8's ten-minute encrypted switch-result envelope;
  ordinary tables, logs, cache and outbox never store plaintext tokens.
  Refresh cookies are HttpOnly, Secure in non-local environments, SameSite
  Strict and scoped to `/api/v1/auth/`.
- Password and MFA recovery use distinct purpose-bound tokens, generic `202`
  starts and encrypted one-time delivery envelopes. Raw selector/verifier
  material never appears in API responses, audit, logs or ordinary outbox
  payloads.
- Every read of global Identity email is audit-before-PII: enumerate only
  non-PII Identity IDs, durably commit append-only `who/why/request_id/
  correlation_id` records through `PiiAccessAuditService`, and only then issue
  the email-bearing query. Collision reports and tenant user list/user-info
  fail closed without reading or returning email if audit persistence fails;
  raw email is never stored in audit metadata.
- The frozen `/api/v1/auth/refresh` request-body token remains a deprecated v1
  compatibility input with token-free usage telemetry; the HttpOnly cookie
  takes precedence. Removing the body property or behavior requires an
  ADR-backed `/api/v2` contract and is outside Plan 1.
- Rate-limit keys are independent: login 5 per normalized email or normalized
  legacy username and 30 per trusted gateway IP per 15 minutes; MFA 5 per
  challenge per 10 minutes; recovery 3 per normalized email and 20 per trusted
  gateway IP per hour. Client-supplied forwarding headers are never the IP
  authority.
- Every deferred source alias also consumes a canonical-Identity login bucket;
  N legacy usernames can never multiply password/MFA attempts against one
  canonical credential authority.
- Every merge approval/completion, credential synchronization, finalization,
  hold latch and security mutation that touches a merge graph uses one lock
  coordinator. It discovers the complete connected graph (including realized
  finalized edges), takes transaction advisory locks first by sorted User-PK
  bootstrap key and then sorted Identity UUID, locks all `User` rows `ORDER BY
  pk`, operation rows `ORDER BY id`, then security/tenant rows by the
  closed table-rank and PK order. It re-reads the graph, versions and holds
  before CAS; graph expansion causes typed retry, never an incremental
  out-of-order lock.
- `IAM_DEFERRED_MERGE_EXECUTION_ENABLED=true` is necessary but insufficient.
  Startup and every convergence/merge execution verify an Ed25519-signed,
  unexpired coverage attestation bound to the exact runtime entry-point
  manifest and build commit. Invalid or stale evidence makes effective
  execution false.
- Command-side mutations use specialized CAS repositories. Direct `.save()`/`.delete()` is allowed only in Django test setup and migration code.
- `revoke_all_sessions()` only revokes sessions. Exactly one
  `invalidate_identity_security_context()` operation owns the atomic
  `security_version` increment plus all-session revoke; logout-all and every
  runtime credential/MFA invalidation use it. The signed Release N email
  reconciliation command is the sole pre-authority migration exception and
  runs before IdentitySession issuance is enabled.
- Post-backfill global IAM gates enumerate tenants only through the
  `DurableIamTenantInventoryProvider` implementation of the narrow Protocol,
  then enter each tenant's RLS context for membership/grant/policy queries. No
  command receives an unscoped tenant manager.
- All tests run in Docker against PostgreSQL/PostGIS; SQLite is forbidden.
- Migration `0003` and `0004` have exact reverse operations and a PostgreSQL
  `0002 -> 0003 -> 0004 -> 0003 -> 0002` round-trip test. Migration `0005`
  raises `IrreversibleError` on reverse and is the sole narrowly approved
  ADR-0002 exception, usable only after the signed N+3 cutoff gate, zero legal
  holds and recorded backup/retention evidence.
- `apps.common.api.auth` depends only on
  `apps.common.contracts.auth_context.AuthContextValidationProtocol` and its
  frozen DTO. The users implementation is registered once in `UsersConfig.ready()`;
  common code never imports `apps.users` and rejects requests while
  unconfigured.
- Preserve unrelated dirty `requests/*` worktree files.

## Release Boundaries

| Release | Tasks | Gate |
| --- | --- | --- |
| N — Expand | 1–2 | Nullable/non-unique schema deployed; report and pending approvals allowed; actual convergence/merge remains off until runtime coverage attestation passes |
| N+1 — Backfill | 3, then production reconciliation | Every Identity and membership backfilled; unresolved collision and invalid groups = 0 |
| N+2 — Contract and auth foundation | 4–13 | Final constraints validated; new auth stays `SHADOW` until security acceptance |
| Stabilization | 14 | `ENFORCE` soak and all durable/runtime gates satisfied |
| N+3a — Membership authority compatibility | 15 | Membership-backed list/admin/user-info and PII context isolation live while temporary claims and legacy validator remain |
| N+3a — Signed JWT cutoff and drain | 16 | Consumer inventory approved; claim/issuer/event cutoff deployed; old sessions and refresh state drained; seven-day zero-use soak complete |
| N+3b — Irreversible schema retirement | 17 | Fresh zero-hold/backup/retention gate approved; `0005` deployed as a separate migration artifact; rollback is identity-only |

## File Structure

- Modify `backend/apps/users/models.py`: keep `User`; add nullable expand fields and re-export IAM model classes.
- Create `backend/apps/users/models_identity.py`: global durable IAM tenant
  registry plus pre-auth, MFA, recovery, session, switch-operation,
  reconciliation and auth-audit records.
- Create `backend/apps/users/models_membership.py`: tenant membership, authorization state and session-grant records.
- Create `backend/apps/users/migrations/0003_identity_expand.py`: nullable/non-unique expand schema and new-table RLS.
- Create `backend/apps/users/security/iam_retirement_barrier.py`: shared
  advisory-lock identifiers/helpers used by hold latches and irreversible
  retirement; migration SQL freezes the same constants.
- Create `backend/apps/users/tests/conftest.py` and
  `backend/apps/users/tests/iam_factories.py`: the shared JSON client, explicit
  IAM factories and task-owned fixtures used by the tests below.
- Create `backend/apps/users/migrations/0004_identity_contract_constraints.py`: separately released concurrent unique indexes, validated non-null checks and foreign keys.
- Create `backend/apps/users/migrations/0005_retire_legacy_user_authority.py`:
  separately gated removal of inactive tenant/MFA/device columns after their
  read/write paths are removed.
- Create `backend/apps/users/services/email_normalization.py`,
  `services/identity_foundation.py`,
  `services/pii_access_audit_service.py`,
  `management/commands/report_identity_email_duplicates.py` and
  `resolve_identity_collision.py`: candidate calculation, protected report and
  approved typed resolution.
- Create `backend/apps/users/tasks/identity_backfill.py` and `management/commands/backfill_identity_foundation.py`: batched idempotent backfill.
- Create focused repositories under `backend/apps/users/repositories/`: identity security, pre-auth, MFA/recovery, sessions, memberships and grant switching.
- Create `backend/apps/common/contracts/iam_tenant_inventory.py`,
  `backend/apps/common/contracts/auth_context.py` and
  `backend/apps/common/contracts/pii_context.py`, and
  `backend/apps/users/infrastructure/iam_tenant_inventory.py`: the shared
  tenant inventory Protocol, one-time compatibility discovery and durable
  registry provider used by post-backfill global IAM gates.
- Create auth services under `backend/apps/users/services/`: token codec, pre-auth flow, session/refresh rotation, tenant switch, recovery, security controls and audit.
- Create `backend/apps/users/tasks/session_reconciliation.py`: fail-closed
  retry/backoff/DLQ cleanup for DB/Redis divergence.
- Modify `backend/apps/users/api.py`, `schemas/auth.py`,
  `backend/apps/common/api/auth.py`, `backend/apps/common/api/errors.py`,
  `backend/apps/common/context/guard.py` and
  `backend/apps/common/middleware/context_isolation.py`: staged API, typed
  auth errors and request-scoped PII-context lifecycle.
- Modify `backend/config/api.py` and `backend/config/urls.py`: composition-root
  registration happens before any router imports the common JWT singleton.
- Modify `infra/api_gateway/kong.yml`: split the exact tenant-selection,
  context-free auth and protected API routes; clear client context headers and
  set them only after route-appropriate JWT handling.
- Modify `backend/config/settings/base.py`, `local.py`, `prod.py` and `backend/apps/users/apps.py`: feature mode, keyrings and startup checks.
- Modify frontend auth API/store/login flow and regenerate `frontend/src/shared/api/generated/`.

### Shared test-harness contract

Task 1 creates `backend/apps/users/tests/conftest.py` and
`backend/apps/users/tests/iam_factories.py`; every later task extends those
files **before** collecting its failing tests and includes the changed harness
in that task's commit. Fixtures are not assumed to exist in the repository or
in root `backend/conftest.py`. Ownership is explicit:

| Task | Fixtures/factories added before its test step |
| --- | --- |
| 1 | `identity`, `recovery_token`, `identity_merge_operation_factory`, `iam_nobypassrls_role`, `two_tenant_rls_rows`, `iam_migration_connection`; deterministic UUID/time helpers |
| 2 | `collision_pair`, `signed_reconciliation_approval`, `identity_collision_resolution_service`, `viewer_role`, `legacy_user_without_identity_foundation` (with non-null legacy tenant), `protected_report_dir`, `pending_merge_with_distinct_credentials`, `dormant_pending_merge`, `held_pending_merge`, `valid_runtime_merge_coverage_attestation`, PostgreSQL concurrency barriers |
| 3 | `identity_expand_apps`, `admin_actor`, `approved_merged_collision_state`; reuse Task 1/2 identities and roles |
| 4 | `active_membership`, `identity_session`, `active_grant`, `role`; tenant-context factory |
| 5 | legal-hold/session factories and transaction barriers |
| 6 | `identity_with_confirmed_factor`, `identity_without_factor`, `fake_mfa_enrollment`, accepting/rejecting factor verifiers |
| 7 | `active_session_context`, `authenticated_identity`, isolated Redis fake and `failing_redis` |
| 8 | single/two-membership session/switch-operation factories (including an initially expired open grant), typed failure/race states, reconciliation proofs/rows, selection claims/tokens and Redis doubles used by all three Task 8 test modules |
| 9 | TOTP/backup-code/setup/recovery-provider factories, `request_trace_context` plus module-autouse `recovery_request_context`, `failing_pii_audit`, recovery decrypt spy, global-stream decoder and captured-log helpers |
| 10 | `raw_client`, `authenticated_client`, gateway security doubles and unavailable limiter |
| 11 | audit/event/live-identity/password/recovery factories |
| 12 | `client`, `client_factory`, `gateway_client`, `authenticated_client`, `authenticated_identity_session`, `merged_identity_access_context`, isolated rate-limit state, access/selection/refresh token factories, API operation-state factories, metrics registry and service/limiter-failure injectors |
| 13–14 | rollout and stabilization factories named in those task tests |
| 15 | `merged_membership_compatibility_state`, authenticated PII request/system scopes, audit/query spies and two-tenant concurrent request helpers |
| 16 | candidate/cutoff access tokens, protected backend/Kong clients, legacy-session/refresh/event-consumer states and signed JWT inventory evidence |
| 17 | held-legacy Identity plus isolated `0004`/`0005` migration-state helpers; reuse Task 1 `iam_migration_connection` |

Every shared or task-local fixture that creates a tenant IAM row first creates
or idempotently registers its `IamTenantRegistryEntry` in the same transaction.
The only exception is a test whose explicit purpose is to prove the deferred
registry FK rejects an unregistered tenant.

`pytest` parameter names (`self`, `column`, `claim`, `code`, `value`) remain
local unittest/parametrize inputs and are not fixtures. Helper calls such as
`login_partial`, `current_totp`, `switch_fixture_a_to_b`,
`refresh_grant` and `redis_refresh_state` live in `iam_factories.py`, are
imported explicitly by each test module and never arrive through hidden global
state. Each task's red step must first run `pytest --fixtures-per-test` (or
collection-only) for its files and have zero unknown fixtures; the intended
failure must then be the missing production behavior, not test harness setup.

`iam_migration_connection` is a per-test PostgreSQL-only migration harness. It
creates a random explicitly named schema on the same test server, registers a
temporary Django database alias whose `search_path` starts with that schema,
and yields its `DatabaseWrapper`; teardown closes the wrapper, unregisters the
alias and drops only that exact schema. It begins with no migration history and
is **never** pointed at the default test schema. All `MigrationExecutor` tests
in Tasks 3, 4 and 17 use it, so an irreversible default-schema `0005` can never
be crossed merely to test `0003`/`0004`. The fixture refuses non-PostgreSQL
engines and validates/quotes its generated schema identifier.

---

### Task 1: Expand Schema Without Final Identity Constraints

**Files:**

- Modify: `backend/apps/users/models.py`
- Create: `backend/apps/users/models_identity.py`
- Create: `backend/apps/users/models_membership.py`
- Create: `backend/apps/users/migrations/0003_identity_expand.py`
- Create: `backend/apps/users/security/iam_retirement_barrier.py`
- Create: `backend/apps/users/tests/conftest.py`
- Create: `backend/apps/users/tests/iam_factories.py`
- Test: `backend/apps/users/tests/test_identity_expand_schema.py`

**Interfaces:**

- Produces nullable, non-unique `User.identity_id`, `User.normalized_email`,
  `User.email_normalization_version`, `User.security_version`,
  `User.security_status` and `User.identity_legal_hold`; Release N adds no
  default or `NOT NULL` column to active `users_users`.
- Produces closed `User.SecurityStatus` choices and `PreAuthChallenge.FlowKind`,
  including a conditionally required merge-operation/version/canonical-
  security/idempotency binding for `MERGE_CONVERGENCE` only.
- Produces global UUID-PK models `PreAuthChallenge`, `IdentitySession`,
  `MfaCredential`, `BackupCode`, `RecoveryToken`, `RecoveryOperation`,
  `TenantGrantSwitchOperation`, `SessionReconciliationRecord`,
  `IdentityMergeOperation`, `IdentityMergeHoldBlock`,
  `IdentityMergeRequeue`, `IdentityMergeRequeueResult` and
  `IdentitySecurityAuditRecord`.
- Produces append-only global `IamTenantRegistryEntry(tenant_id UUID PRIMARY
  KEY, registered_at, version, source_code)` without RLS or tenant business
  data. It is inventory only, never request authorization authority.
- Produces tenant models `TenantMembership`, `TenantAuthorizationState`,
  `TenantSessionGrant`; the expand-state membership has a nullable,
  write-once `legacy_actor_user` FK to the existing integer-PK User row.
  Task 3 fills it and Task 4 makes it non-null after preflight.
- Produces direct FK `TenantSessionGrant.session -> IdentitySession.id` and `TenantSessionGrant.membership -> TenantMembership.id`.
- Produces deferrable database FKs from each Plan 1 tenant IAM `tenant_id` and
  switch expected/target/result tenant reference to
  `IamTenantRegistryEntry.tenant_id`; registry insertion precedes tenant writes
  in services, and the deferred constraint is the commit-time completeness
  guard.
- Produces the shared `JsonTestClient`, `client` and `client_factory` contract
  shown in Task 10 plus explicit Task 1 factories; later tasks extend rather
  than replace this harness.
- Produces globally unique `IdentitySession.session_family_id`, one open
  recovery token per Identity/purpose, immutable OneToOne
  `RecoveryToken.operation -> RecoveryOperation`, typed reconciliation rows,
  database recovery-binding integrity triggers and append-only grant/audit
  triggers.
- Produces `IAM_RETIREMENT_BARRIER` shared/exclusive advisory helpers and DB
  triggers on every IAM legal-hold latch. The frozen signed-bigint key is
  `IAM_RETIREMENT_BARRIER_KEY = 0x49414D5F52455449`; historical migrations
  embed the same literal rather than importing runtime code. The trigger
  takes a transaction shared lock for insert-with-hold and
  `FALSE/NULL -> TRUE`; schema tests fail if any hold column lacks it. Task 17
  takes the exclusive side before DDL.
- Defers Identity-target FKs until Task 4 because `users.User.identity_id` is not unique during expand.
- `0003` stores `IdentityMergeOperation.source_identity_id` and
  `canonical_identity_id` as plain UUID columns without foreign keys.
  `source_user` is the only immediate merge FK because it targets the existing
  integer primary key. Task 4 creates Identity uniqueness first and only then
  adds/validates the two UUID-target FKs.

- [x] **Step 1: Write failing expand-schema tests**

First create the Task 1 `identity` and `recovery_token` factories in the shared
test harness, then create
`backend/apps/users/tests/test_identity_expand_schema.py`:

```python
import json
import uuid
from datetime import timedelta

import pytest
from django.db import DatabaseError, IntegrityError, connection, models, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.fields import NOT_PROVIDED
from django.utils import timezone

from apps.common.tenancy.context import clear_tenant_context, set_tenant_context
from apps.users.models import (
    IdentitySession,
    IamTenantRegistryEntry,
    IdentityMergeHoldBlock,
    IdentityMergeOperation,
    IdentityMergeRequeue,
    IdentityMergeRequeueResult,
    MfaCredential,
    PreAuthChallenge,
    RecoveryOperation,
    RecoveryToken,
    SessionReconciliationRecord,
    TenantAuthorizationState,
    TenantGrantSwitchOperation,
    TenantMembership,
    TenantSessionGrant,
    User,
)


@pytest.mark.django_db
def test_identity_expand_fields_are_nullable_and_not_unique():
    executor = MigrationExecutor(connection)
    expand_apps = executor.loader.project_state([("users", "0003_identity_expand")]).apps
    ExpandUser = expand_apps.get_model("users", "User")
    identity_field = ExpandUser._meta.get_field("identity_id")
    email_field = ExpandUser._meta.get_field("normalized_email")

    assert identity_field.null is True
    assert identity_field.unique is False
    assert identity_field.db_index is False
    assert email_field.null is True
    assert email_field.unique is False
    assert email_field.db_index is False
    assert ExpandUser._meta.get_field("security_status").db_index is False
    legal_hold_field = ExpandUser._meta.get_field("identity_legal_hold")
    assert legal_hold_field.null is True
    assert legal_hold_field.default is NOT_PROVIDED
    assert legal_hold_field.db_index is False


@pytest.mark.django_db
def test_global_session_has_no_tenant_column():
    assert "tenant_id" not in {field.name for field in IdentitySession._meta.fields}


def test_durable_tenant_registry_is_global_append_only_inventory():
    assert IamTenantRegistryEntry._meta.pk.name == "tenant_id"
    assert {field.name for field in IamTenantRegistryEntry._meta.fields} == {
        "tenant_id",
        "registered_at",
        "version",
        "source_code",
    }


@pytest.mark.django_db
def test_membership_has_no_email_authority():
    names = {field.name for field in TenantMembership._meta.fields}
    assert "email" not in names
    assert {
        "tenant_id",
        "identity_id",
        "legacy_actor_user",
        "display_name",
        "position",
        "status",
        "authz_version",
    } <= names
    actor = TenantMembership._meta.get_field("legacy_actor_user")
    assert actor.related_model is User
    assert actor.remote_field.on_delete is models.PROTECT
    assert actor.null is True


@pytest.mark.django_db
def test_grant_has_real_session_and_membership_foreign_keys():
    assert TenantSessionGrant._meta.get_field("session").related_model is IdentitySession
    assert TenantSessionGrant._meta.get_field("membership").related_model is TenantMembership


def test_session_is_bound_to_identity_security_version():
    assert IdentitySession._meta.get_field("security_version_snapshot")
    assert IdentitySession._meta.get_field("session_family_id").unique is True


def test_security_models_expose_required_database_constraints():
    membership_constraints = {
        constraint.name for constraint in TenantMembership._meta.constraints
    }
    grant_constraints = {
        constraint.name for constraint in TenantSessionGrant._meta.constraints
    }
    tenant_state_constraints = {
        constraint.name for constraint in TenantAuthorizationState._meta.constraints
    }
    mfa_constraints = {
        constraint.name for constraint in MfaCredential._meta.constraints
    }
    recovery_constraints = {
        constraint.name for constraint in RecoveryToken._meta.constraints
    }

    assert "ck_users_grant_not_soft_deleted" in grant_constraints
    assert "uniq_users_active_membership_actor" in membership_constraints
    assert "ck_users_tenant_auth_state_not_soft_deleted" in tenant_state_constraints
    assert "uniq_users_unrevoked_totp_per_identity" in mfa_constraints
    assert "uniq_users_open_recovery_per_identity_purpose" in recovery_constraints


def test_recovery_delivery_state_machine_has_single_worker_claim():
    assert {value for value, _ in RecoveryOperation.Status.choices} == {
        "pending_delivery",
        "delivering",
        "delivered",
        "completed",
        "superseded",
        "expired",
        "failed",
    }


def test_recovery_token_is_bound_to_exact_delivery_operation():
    operation_field = RecoveryToken._meta.get_field("operation")
    assert operation_field.related_model is RecoveryOperation
    assert operation_field.one_to_one is True
    operation_constraints = {
        constraint.name for constraint in RecoveryOperation._meta.constraints
    }
    assert "ck_users_recovery_operation_envelope_state" in operation_constraints
    assert "ck_users_recovery_operation_completion_state" in operation_constraints
    assert "ck_users_recovery_operation_lease_state" in operation_constraints
    assert "ck_users_recovery_operation_delivered_state" in operation_constraints


def test_identity_security_status_is_closed_enum():
    assert {value for value, _ in User.SecurityStatus.choices} == {
        "pending_credentials",
        "mfa_setup_required",
        "active",
        "locked",
    }


def test_preauth_merge_flow_has_durable_operation_binding():
    assert "merge_convergence" in {
        value for value, _ in PreAuthChallenge.FlowKind.choices
    }
    assert PreAuthChallenge._meta.get_field("flow_kind").max_length >= len(
        "merge_convergence",
    )
    fields = {field.name for field in PreAuthChallenge._meta.fields}
    assert {
        "merge_operation",
        "merge_operation_version_snapshot",
        "canonical_security_version_snapshot",
        "idempotency_key",
    } <= fields
    assert "ck_users_preauth_merge_binding" in {
        constraint.name for constraint in PreAuthChallenge._meta.constraints
    }


def test_merge_expand_state_has_no_identity_target_foreign_keys():
    operation = IdentityMergeOperation
    assert operation._meta.get_field("source_user").remote_field.on_delete is models.PROTECT
    assert isinstance(operation._meta.get_field("source_identity_id"), models.UUIDField)
    assert isinstance(operation._meta.get_field("canonical_identity_id"), models.UUIDField)
    assert operation._meta.get_field("source_identity_id").remote_field is None
    assert operation._meta.get_field("canonical_identity_id").remote_field is None


def test_merge_state_and_hold_evidence_are_closed_and_append_only():
    assert {value for value, _ in IdentityMergeOperation.State.choices} == {
        "approved_pending_convergence",
        "approved_deferred",
        "ready_to_finalize",
        "finalized",
        "cancelled",
        "expired",
    }
    assert {value for value, _ in IdentityMergeOperation.ConvergenceMode.choices} == {
        "owner_confirmed",
        "dormant_fail_closed",
    }
    assert IdentityMergeHoldBlock._meta.get_field("operation").remote_field.on_delete is models.PROTECT
    assert IdentityMergeRequeue._meta.get_field("hold_block").remote_field.on_delete is models.PROTECT
    assert IdentityMergeRequeueResult._meta.get_field("requeue").one_to_one is True


@pytest.mark.parametrize(
    "initial_state",
    [value for value, _ in IdentityMergeOperation.State.choices],
)
@pytest.mark.django_db(transaction=True)
def test_merge_operation_insert_requires_pending_initial_state(
    identity_merge_operation_factory,
    initial_state,
):
    if initial_state == IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE:
        operation = identity_merge_operation_factory(state=initial_state)
        assert operation.credential_epoch == 0
        assert operation.convergence_mode is None
        assert operation.credentials_changed_at is None
        assert operation.credentials_converged_at is None
        assert operation.finalized_at is None
        return

    with pytest.raises(IntegrityError, match="identity_merge_invalid_initial_state"):
        with transaction.atomic():
            identity_merge_operation_factory(state=initial_state)


MERGE_OPERATION_ALLOWED_TRANSITIONS = {
    (
        IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE,
        IdentityMergeOperation.State.APPROVED_DEFERRED,
    ),
    (
        IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE,
        IdentityMergeOperation.State.CANCELLED,
    ),
    (
        IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE,
        IdentityMergeOperation.State.EXPIRED,
    ),
    (
        IdentityMergeOperation.State.APPROVED_DEFERRED,
        IdentityMergeOperation.State.READY_TO_FINALIZE,
    ),
    (
        IdentityMergeOperation.State.READY_TO_FINALIZE,
        IdentityMergeOperation.State.FINALIZED,
    ),
}
MERGE_OPERATION_TRANSITION_CASES = [
    (source, target)
    for source, _ in IdentityMergeOperation.State.choices
    for target, _ in IdentityMergeOperation.State.choices
    if source != target
]


@pytest.mark.parametrize("source_state,target_state", MERGE_OPERATION_TRANSITION_CASES)
@pytest.mark.django_db(transaction=True)
def test_merge_operation_trigger_enforces_exact_transition_matrix(
    identity_merge_operation_factory,
    source_state,
    target_state,
):
    operation = identity_merge_operation_factory.advance_to(source_state)
    transition = (source_state, target_state)
    now = timezone.now()
    updates = {
        "state": target_state,
        "version": models.F("version") + 1,
        "updated_at": now,
    }
    if target_state == IdentityMergeOperation.State.APPROVED_DEFERRED:
        updates.update(
            convergence_mode=IdentityMergeOperation.ConvergenceMode.OWNER_CONFIRMED,
            credential_epoch=1,
            credentials_changed_at=now,
            credentials_converged_at=now,
        )
    if target_state == IdentityMergeOperation.State.FINALIZED:
        updates["finalized_at"] = now

    if transition in MERGE_OPERATION_ALLOWED_TRANSITIONS:
        assert IdentityMergeOperation.objects.filter(pk=operation.pk).update(**updates) == 1
        operation.refresh_from_db()
        assert operation.state == target_state
        return

    with pytest.raises(IntegrityError, match="identity_merge_invalid_transition"):
        with transaction.atomic():
            IdentityMergeOperation.objects.filter(pk=operation.pk).update(**updates)


def test_membership_status_foundation_includes_draft():
    assert {value for value, _ in TenantMembership.Status.choices} == {
        "draft",
        "invited",
        "active",
        "disabled",
    }


def test_switch_replay_and_reconciliation_constraints_exist():
    switch_fields = {
        field.name for field in TenantGrantSwitchOperation._meta.fields
    }
    reconciliation_constraints = {
        constraint.name
        for constraint in SessionReconciliationRecord._meta.constraints
    }
    assert {
        "expected_refresh_generation",
        "expected_refresh_digest",
        "result_refresh_generation",
        "result_refresh_digest",
        "response_ciphertext",
        "response_nonce",
        "response_key_id",
        "response_expires_at",
    } <= switch_fields
    assert {
        field.name for field in SessionReconciliationRecord._meta.fields
    } >= {"kind", "switch_operation"}
    switch_field = SessionReconciliationRecord._meta.get_field("switch_operation")
    assert switch_field.one_to_one is True
    assert switch_field.target_field.name == "operation_id"
    assert {
        value for value, _ in SessionReconciliationRecord.Kind.choices
    } == {
        "refresh_rotation",
        "tenant_switch",
        "session_family_cleanup",
    }
    assert "uniq_users_session_reconciliation_kind_operation" in (
        reconciliation_constraints
    )
    unique = next(
        constraint
        for constraint in SessionReconciliationRecord._meta.constraints
        if constraint.name == "uniq_users_session_reconciliation_kind_operation"
    )
    assert tuple(unique.fields) == ("session", "kind", "operation_id")
    assert "ck_users_reconciliation_switch_binding" in (
        reconciliation_constraints
    )


@pytest.mark.django_db
def test_grant_append_only_trigger_is_installed():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1
            FROM pg_trigger
            WHERE tgname = 'trg_users_grant_append_only'
              AND NOT tgisinternal
            """
        )
        assert cursor.fetchone() == (1,)


@pytest.mark.django_db(transaction=True)
def test_membership_actor_cannot_be_rebound(identity):
    tenant_id = uuid.uuid4()
    IamTenantRegistryEntry.objects.create(
        tenant_id=tenant_id,
        source_code="test_fixture",
    )
    other_actor = User.objects.create_user(
        username="other-actor",
        password="Temporary123!",
    )
    set_tenant_context(tenant_id)
    try:
        membership = TenantMembership.objects.create(
            tenant_id=tenant_id,
            identity_id=identity.identity_id,
            legacy_actor_user=identity,
            display_name="Ivan",
            status=TenantMembership.Status.ACTIVE,
        )
        with pytest.raises(IntegrityError, match="membership_actor_immutable"):
            with transaction.atomic():
                TenantMembership.objects.filter(pk=membership.pk).update(
                    legacy_actor_user=other_actor,
                )
    finally:
        clear_tenant_context()


@pytest.mark.django_db
def test_identity_audit_append_only_trigger_is_installed():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1
            FROM pg_trigger
            WHERE tgname = 'trg_users_identity_audit_append_only'
              AND NOT tgisinternal
            """
        )
        assert cursor.fetchone() == (1,)


@pytest.mark.django_db
def test_recovery_binding_integrity_triggers_are_installed():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tgname
            FROM pg_trigger
            WHERE tgname IN (
                'trg_users_recovery_token_operation_immutable',
                'trg_users_recovery_operation_requires_token'
            )
              AND NOT tgisinternal
            ORDER BY tgname
            """
        )
        assert [name for (name,) in cursor.fetchall()] == [
            "trg_users_recovery_operation_requires_token",
            "trg_users_recovery_token_operation_immutable",
        ]


@pytest.mark.django_db(transaction=True)
def test_recovery_token_operation_cannot_be_rebound(recovery_token):
    with pytest.raises(IntegrityError, match="recovery_token_operation_immutable"):
        with transaction.atomic():
            replacement = RecoveryOperation.objects.create(
                delivery_ciphertext=b"replacement-ciphertext",
                delivery_nonce=b"nonce-12-byte",
                delivery_key_id="test-recovery-key",
                request_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
                causal_root_id=uuid.uuid4(),
            )
            RecoveryToken.objects.filter(pk=recovery_token.pk).update(
                operation_id=replacement.id,
            )


@pytest.mark.django_db(transaction=True)
def test_deliverable_recovery_operation_cannot_commit_without_token():
    with pytest.raises(IntegrityError, match="recovery_operation_requires_token"):
        with transaction.atomic():
            RecoveryOperation.objects.create(
                delivery_ciphertext=b"orphan-ciphertext",
                delivery_nonce=b"nonce-12-byte",
                delivery_key_id="test-recovery-key",
                request_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
                causal_root_id=uuid.uuid4(),
            )


@pytest.mark.django_db(transaction=True)
def test_operation_and_linked_token_commit_together(identity):
    with transaction.atomic():
        operation = RecoveryOperation.objects.create(
            delivery_ciphertext=b"linked-ciphertext",
            delivery_nonce=b"nonce-12-byte",
            delivery_key_id="test-recovery-key",
            request_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            causal_root_id=uuid.uuid4(),
        )
        token = RecoveryToken.objects.create(
            identity_id=identity.identity_id,
            operation=operation,
            verifier_hash="a" * 64,
            key_id="test-verifier-key",
            purpose=RecoveryToken.Purpose.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(minutes=15),
        )

    operation.refresh_from_db()
    assert operation.token.id == token.id
    assert token.operation_id == operation.id


@pytest.mark.django_db
def test_reconciliation_switch_session_trigger_is_installed():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1
            FROM pg_trigger
            WHERE tgname = 'trg_users_reconciliation_switch_session_match'
              AND NOT tgisinternal
            """
        )
        assert cursor.fetchone() == (1,)


@pytest.mark.django_db
def test_tenant_tables_have_rls_enabled():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT relname, relrowsecurity, relforcerowsecurity
            FROM pg_class
            WHERE relname IN (
                'users_tenant_memberships',
                'users_tenant_authorization_state',
                'users_tenant_session_grants'
            )
            ORDER BY relname
            """
        )
        rows = cursor.fetchall()

    assert rows
    assert all(rls and force_rls for _, rls, force_rls in rows)


@pytest.mark.parametrize(
    "table_name",
    [
        "users_tenant_memberships",
        "users_tenant_authorization_state",
        "users_tenant_session_grants",
    ],
)
@pytest.mark.django_db(transaction=True)
def test_tenant_tables_reject_cross_tenant_read_and_write_under_nobypass_role(
    table_name,
    iam_nobypassrls_role,
    two_tenant_rls_rows,
):
    tenant_a = two_tenant_rls_rows.tenant_a
    tenant_b_row_id = two_tenant_rls_rows.row_id(table_name, tenant="b")
    insert_sql, insert_params = two_tenant_rls_rows.cross_tenant_insert(
        table_name,
        tenant="b",
    )

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(f'SET LOCAL ROLE "{iam_nobypassrls_role}"')
            cursor.execute(
                "SET LOCAL app.current_tenant_id = %s",
                [str(tenant_a)],
            )
            cursor.execute(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user",
            )
            assert cursor.fetchone() == (False, False)
            cursor.execute(f"SELECT DISTINCT tenant_id FROM {table_name}")
            assert cursor.fetchall() == [(tenant_a,)]
            cursor.execute(
                f"UPDATE {table_name} SET updated_at = updated_at WHERE id = %s",
                [str(tenant_b_row_id)],
            )
            assert cursor.rowcount == 0
            with pytest.raises(DatabaseError) as caught:
                with transaction.atomic():
                    cursor.execute(insert_sql, insert_params)
            assert caught.value.__cause__.sqlstate == "42501"


@pytest.mark.django_db(transaction=True)
def test_authorization_state_is_unique_per_tenant():
    tenant_id = uuid.uuid4()
    IamTenantRegistryEntry.objects.create(
        tenant_id=tenant_id,
        source_code="test_fixture",
    )
    set_tenant_context(tenant_id)
    try:
        TenantAuthorizationState.objects.create(
            tenant_id=tenant_id,
            policy_version=1,
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                TenantAuthorizationState.objects.create(
                    tenant_id=tenant_id,
                    policy_version=2,
                )
    finally:
        clear_tenant_context()


@pytest.mark.django_db(transaction=True)
def test_only_one_open_grant_exists_per_session(identity):
    tenant_id = uuid.uuid4()
    IamTenantRegistryEntry.objects.create(
        tenant_id=tenant_id,
        source_code="test_fixture",
    )
    session = IdentitySession.objects.create(
        identity_id=identity.identity_id,
        security_version_snapshot=1,
        expires_at=timezone.now() + timedelta(days=7),
    )
    set_tenant_context(tenant_id)
    try:
        membership = TenantMembership.objects.create(
            tenant_id=tenant_id,
            identity_id=identity.identity_id,
            legacy_actor_user_id=identity.pk,
            display_name="RLS test identity",
            status=TenantMembership.Status.ACTIVE,
        )
        grant_values = {
            "tenant_id": tenant_id,
            "session": session,
            "membership": membership,
            "issued_at": timezone.now(),
            "expires_at": timezone.now() + timedelta(hours=8),
        }
        TenantSessionGrant.objects.create(context_version=1, **grant_values)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                TenantSessionGrant.objects.create(
                    context_version=2,
                    **grant_values,
                )
    finally:
        clear_tenant_context()
```

The session-scoped `iam_nobypassrls_role` fixture is created by the test
database owner with fixed identifier `complect_iam_rls_test`, attributes
`NOLOGIN NOSUPERUSER NOBYPASSRLS`, and only the sequence/table privileges
needed by these tests. The fixture validates those flags before yielding;
every negative assertion runs after `SET LOCAL ROLE`, never as
`complect_admin`. `two_tenant_rls_rows` seeds valid A/B graphs before role
switching and returns only fixed, allowlisted SQL per table. Each proposed
cross-tenant insert satisfies every FK/unique/check invariant and differs only
by its tenant, so the asserted SQLSTATE `42501` can come only from RLS, not a
secondary constraint; no arbitrary table name or RLS-disable helper is
exposed. This behavioral matrix is mandatory in
addition to the `pg_class` metadata assertion.

Add `test_tenant_registry_constraints_are_deferred_and_enforced`: a
membership/auth-state/grant or switch tenant reference without a registry row
must fail at transaction commit, while registry-first creation succeeds.
Add `test_every_iam_hold_column_has_retirement_barrier_trigger`: introspect
`information_schema.columns`/`pg_trigger`, enumerate `identity_legal_hold` plus
every Plan 1 `is_legal_hold` column, and require the frozen shared-barrier
trigger on each. A two-connection test holds the exclusive advisory lock in A,
attempts a legal-hold latch in B with bounded `lock_timeout`, and proves B
cannot commit or mutate the row until A releases it.

- [x] **Step 2: Run the test and verify it fails**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_identity_expand_schema.py -v
```

Expected: import/model failures because the expand models do not exist.

- [x] **Step 3: Add nullable Identity fields**

In `backend/apps/users/models.py` add no default and no uniqueness:

```python
class User(AbstractUser):
    class SecurityStatus(models.TextChoices):
        PENDING_CREDENTIALS = "pending_credentials"
        MFA_SETUP_REQUIRED = "mfa_setup_required"
        ACTIVE = "active"
        LOCKED = "locked"

    identity_id = models.UUIDField(null=True, blank=True, editable=False)
    normalized_email = models.EmailField(max_length=254, null=True, blank=True, editable=False)
    email_normalization_version = models.PositiveSmallIntegerField(null=True, editable=False)
    security_version = models.PositiveBigIntegerField(null=True, editable=False)
    security_status = models.CharField(
        max_length=32,
        choices=SecurityStatus.choices,
        null=True,
        editable=False,
    )
    identity_legal_hold = models.BooleanField(null=True, editable=False)
    # Legacy tenant/MFA/device fields remain compatibility shadows through
    # Task 16 cutoff/drain and are removed only by Task 17 N+3b.
```

Re-export the new model classes at the bottom of `models.py` so existing import style remains stable:

```python
from apps.users.models_identity import (  # noqa: E402, F401
    BackupCode,
    IamTenantRegistryEntry,
    IdentityMergeHoldBlock,
    IdentityMergeOperation,
    IdentityMergeRequeue,
    IdentityMergeRequeueResult,
    IdentitySecurityAuditRecord,
    IdentitySession,
    MfaCredential,
    PreAuthChallenge,
    RecoveryOperation,
    RecoveryToken,
    SessionReconciliationRecord,
    TenantGrantSwitchOperation,
)
from apps.users.models_membership import (  # noqa: E402, F401
    TenantAuthorizationState,
    TenantMembership,
    TenantSessionGrant,
)
```

- [x] **Step 4: Define global and tenant records**

Define the tenant-free durable registry and abstract security base in
`models_identity.py`:

```python
class IamTenantRegistryEntry(models.Model):
    tenant_id = models.UUIDField(primary_key=True, editable=False)
    registered_at = models.DateTimeField(auto_now_add=True)
    version = models.PositiveBigIntegerField(default=1, editable=False)
    source_code = models.CharField(max_length=32, editable=False)

    class Meta:
        db_table = "users_iam_tenant_registry"


class GlobalSecurityRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.PositiveBigIntegerField(default=1, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_legal_hold = models.BooleanField(default=False, db_index=True)

    class Meta:
        abstract = True
```

The expand migration also creates the merge authority before any approved
collision can change runtime identity resolution:

```python
class MergeHoldSubjectType(models.TextChoices):
    OPERATION = "operation"
    SOURCE_IDENTITY = "source_identity"
    CANONICAL_IDENTITY = "canonical_identity"
    MEMBERSHIP = "membership"
    SESSION = "session"
    GRANT = "grant"


class MergeRequeueOutcome(models.TextChoices):
    APPLIED = "applied"
    HOLD_STILL_ACTIVE = "hold_still_active"
    VERSION_CONFLICT = "version_conflict"
    INVALID_RELEASE_EVIDENCE = "invalid_release_evidence"


class IdentityMergeOperation(GlobalSecurityRecord):
    class State(models.TextChoices):
        APPROVED_PENDING_CONVERGENCE = "approved_pending_convergence"
        APPROVED_DEFERRED = "approved_deferred"
        READY_TO_FINALIZE = "ready_to_finalize"
        FINALIZED = "finalized"
        CANCELLED = "cancelled"
        EXPIRED = "expired"

    class ConvergenceMode(models.TextChoices):
        OWNER_CONFIRMED = "owner_confirmed"
        DORMANT_FAIL_CLOSED = "dormant_fail_closed"

    source_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="source_merge_operations",
    )
    source_identity_id = models.UUIDField()
    canonical_identity_id = models.UUIDField(db_index=True)
    state = models.CharField(max_length=40, choices=State.choices)
    convergence_mode = models.CharField(
        max_length=32,
        choices=ConvergenceMode.choices,
        null=True,
    )
    approval_reference = models.CharField(max_length=128)
    approval_digest = models.CharField(max_length=64)
    approval_key_id = models.CharField(max_length=64)
    approval_signature = models.BinaryField()
    approved_at = models.DateTimeField()
    approval_expires_at = models.DateTimeField()
    synthetic_normalized_email = models.EmailField(max_length=254)
    credential_epoch = models.PositiveBigIntegerField(default=0)
    credentials_changed_at = models.DateTimeField(null=True)
    credentials_converged_at = models.DateTimeField(null=True)
    finalized_at = models.DateTimeField(null=True)
    retention_until = models.DateTimeField()
    legal_hold_reference = models.CharField(max_length=128, blank=True, default="")
    legal_hold_applied_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "users_identity_merge_operations"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(source_identity_id=models.F("canonical_identity_id")),
                name="ck_users_merge_source_not_canonical",
            ),
            models.UniqueConstraint(
                fields=["source_user"],
                condition=~models.Q(state__in=["cancelled", "expired"]),
                name="uniq_users_live_merge_source",
            ),
            models.UniqueConstraint(
                fields=["canonical_identity_id"],
                condition=models.Q(state="approved_pending_convergence"),
                name="uniq_users_pending_merge_per_canonical",
            ),
            models.UniqueConstraint(
                fields=["synthetic_normalized_email"],
                condition=~models.Q(state__in=["cancelled", "expired"]),
                name="uniq_users_live_merge_synthetic_email",
            ),
        ]


class IdentityMergeHoldBlock(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.ForeignKey(IdentityMergeOperation, on_delete=models.PROTECT)
    subject_type = models.CharField(max_length=32, choices=MergeHoldSubjectType.choices)
    subject_identity_id = models.UUIDField()
    hold_reference = models.CharField(max_length=128)
    hold_digest = models.CharField(max_length=64)
    action_digest = models.CharField(max_length=64)
    lock_plan_digest = models.CharField(max_length=64)
    hold_set_digest = models.CharField(max_length=64)
    operation_version_snapshot = models.PositiveBigIntegerField()
    request_id = models.UUIDField()
    correlation_id = models.UUIDField()
    observed_at = models.DateTimeField()

    class Meta:
        db_table = "users_identity_merge_hold_blocks"
        constraints = [
            models.UniqueConstraint(
                fields=["operation", "subject_type", "subject_identity_id", "hold_digest"],
                name="uniq_users_merge_hold_observation",
            ),
        ]


class IdentityMergeRequeue(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.ForeignKey(IdentityMergeOperation, on_delete=models.PROTECT)
    hold_block = models.ForeignKey(IdentityMergeHoldBlock, on_delete=models.PROTECT)
    prior_action_digest = models.CharField(max_length=64)
    fresh_action_digest = models.CharField(max_length=64)
    action_schema_version = models.PositiveSmallIntegerField()
    environment = models.CharField(max_length=32)
    execution_build_digest = models.CharField(max_length=64)
    approval_reference = models.CharField(max_length=128)
    key_id = models.CharField(max_length=64)
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    signature = models.BinaryField()
    expected_operation_version = models.PositiveBigIntegerField()
    requested_by = models.CharField(max_length=128)
    request_id = models.UUIDField()
    correlation_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "users_identity_merge_requeues"
        constraints = [
            models.UniqueConstraint(
                fields=["hold_block", "fresh_action_digest"],
                name="uniq_users_merge_release_requeue",
            ),
        ]


class IdentityMergeRequeueResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requeue = models.OneToOneField(IdentityMergeRequeue, on_delete=models.PROTECT)
    outcome = models.CharField(max_length=32, choices=MergeRequeueOutcome.choices)
    observed_operation_version = models.PositiveBigIntegerField()
    processed_at = models.DateTimeField()

    class Meta:
        db_table = "users_identity_merge_requeue_results"
```

Absence of an operation is the only `NONE` state. `source_user_id`, both
Identity UUIDs, approval fields and `synthetic_normalized_email` are immutable.
The source uniqueness constraint covers every state except terminal
pre-convergence `CANCELLED`/`EXPIRED`: those outcomes may be followed by one
new separately signed operation, while a deferred/ready/finalized source can
never be enrolled again. The synthetic-email reservation has the same partial
predicate, so terminal pre-convergence evidence releases the deterministic
source reservation without deletion. `approval_expires_at` is signed and immutable;
expiry is a CAS to `EXPIRED`, not a clock-dependent implicit state.
The ordinary update trigger permits only `state`, the one-time
`convergence_mode NULL -> value`, `credential_epoch`,
`credentials_changed_at`, `credentials_converged_at`, `finalized_at`,
`version` and `updated_at`, and only while the operation itself is not held.
The dedicated legal-governance function is the sole path that may latch or
release the operation's hold fields. Hold block, requeue and requeue-result
tables are append-only; a worker claims a requeue by inserting its unique
OneToOne result in the same transaction as the re-evaluated CAS. A crash before
commit leaves no result and is safely retryable.
An immediate/deferred integrity trigger rejects a requeue unless
`requeue.operation_id == hold_block.operation_id`,
`requeue.prior_action_digest == hold_block.action_digest` and
`issued_at < expires_at`; cryptographic validity remains the service boundary.

`APPROVED_PENDING_CONVERGENCE` changes no external authority.
`APPROVED_DEFERRED` is reachable exactly once with `credential_epoch=1` and
non-null convergence mode/timestamp. The closed transition set is pending to
`APPROVED_DEFERRED`, `CANCELLED` or `EXPIRED`, followed only by
`APPROVED_DEFERRED -> READY_TO_FINALIZE -> FINALIZED`. `FINALIZED`,
`CANCELLED` and `EXPIRED` are terminal. There is no
`BLOCKED_BY_LEGAL_HOLD` state and no
`blocked_by_hold_at` mutation: hold observation lives only in the separate
append-only evidence rows.

`PreAuthChallenge` contains `identity_id`, `token_digest`,
`security_version_snapshot`, optional merge-operation/version/canonical-
security/idempotency binding, `attempt_count`, `mfa_verified_at`, `consumed_at`,
`revoked_at`, `expires_at`. `IdentitySession` contains
`identity_id`, `session_family_id`, `security_version_snapshot`,
`device_metadata`, `refresh_generation`, `current_refresh_digest`,
`refresh_key_id`, `revoked_at`, `expires_at`. `TenantGrantSwitchOperation`
contains globally unique `operation_id`, request fingerprint, expected current
grant/tenant/context, target tenant, status, result grant/tenant/context and
failure code. It also stores a short-lived AES-GCM response envelope
(`response_ciphertext`, `response_nonce`, `response_key_id`,
`response_expires_at`) containing only the exact access token, refresh token
and CSRF value minted for that operation. The envelope is never exposed to
ORM repr/logging and is cleared by retention after the idempotency window.

Declare the fields explicitly:

```python
class PreAuthChallenge(GlobalSecurityRecord):
    class FlowKind(models.TextChoices):
        MFA_VERIFY = "mfa_verify"
        MFA_SETUP = "mfa_setup"
        MERGE_CONVERGENCE = "merge_convergence"

    identity_id = models.UUIDField(db_index=True)
    flow_kind = models.CharField(max_length=32, choices=FlowKind.choices)
    token_digest = models.CharField(max_length=64, unique=True)
    security_version_snapshot = models.PositiveBigIntegerField()
    merge_operation = models.ForeignKey(
        IdentityMergeOperation,
        on_delete=models.PROTECT,
        null=True,
    )
    merge_operation_version_snapshot = models.PositiveBigIntegerField(null=True)
    canonical_security_version_snapshot = models.PositiveBigIntegerField(null=True)
    idempotency_key = models.UUIDField(null=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    mfa_verified_at = models.DateTimeField(null=True)
    consumed_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "users_pre_auth_challenges"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        flow_kind="merge_convergence",
                        merge_operation__isnull=False,
                        merge_operation_version_snapshot__isnull=False,
                        canonical_security_version_snapshot__isnull=False,
                        idempotency_key__isnull=False,
                    )
                    | models.Q(
                        flow_kind__in=["mfa_verify", "mfa_setup"],
                        merge_operation__isnull=True,
                        merge_operation_version_snapshot__isnull=True,
                        canonical_security_version_snapshot__isnull=True,
                        idempotency_key__isnull=True,
                    )
                ),
                name="ck_users_preauth_merge_binding",
            ),
            models.UniqueConstraint(
                fields=["merge_operation", "idempotency_key"],
                condition=models.Q(flow_kind="merge_convergence"),
                name="uniq_users_merge_convergence_challenge_key",
            ),
        ]


class IdentitySession(GlobalSecurityRecord):
    identity_id = models.UUIDField(db_index=True)
    session_family_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    security_version_snapshot = models.PositiveBigIntegerField()
    device_metadata = models.JSONField(default=dict)
    refresh_generation = models.PositiveBigIntegerField(default=0)
    current_refresh_digest = models.CharField(max_length=64, blank=True, default="")
    refresh_key_id = models.CharField(max_length=64, blank=True, default="")
    revoked_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "users_identity_sessions"


class MfaCredential(GlobalSecurityRecord):
    identity_id = models.UUIDField(db_index=True)
    setup_challenge = models.OneToOneField(
        PreAuthChallenge,
        null=True,
        on_delete=models.PROTECT,
        related_name="pending_mfa_credential",
    )
    credential_type = models.CharField(max_length=16, default="totp")
    encrypted_secret = models.BinaryField()
    nonce = models.BinaryField()
    key_id = models.CharField(max_length=64)
    confirmed_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "users_mfa_credentials"
        constraints = [
            models.UniqueConstraint(
                fields=["identity_id"],
                condition=models.Q(
                    credential_type="totp",
                    revoked_at__isnull=True,
                ),
                name="uniq_users_unrevoked_totp_per_identity",
            ),
        ]


class BackupCode(GlobalSecurityRecord):
    identity_id = models.UUIDField(db_index=True)
    code_hash = models.CharField(max_length=64, unique=True)
    key_id = models.CharField(max_length=64)
    used_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "users_backup_codes"


class RecoveryToken(GlobalSecurityRecord):
    class Purpose(models.TextChoices):
        PASSWORD_RESET = "password_reset"
        MFA_RESET = "mfa_reset"

    identity_id = models.UUIDField(db_index=True)
    operation = models.OneToOneField(
        "RecoveryOperation",
        on_delete=models.PROTECT,
        related_name="token",
    )
    verifier_hash = models.CharField(max_length=64, unique=True)
    key_id = models.CharField(max_length=64)
    purpose = models.CharField(max_length=32, choices=Purpose.choices)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "users_recovery_tokens"
        constraints = [
            models.UniqueConstraint(
                fields=["identity_id", "purpose"],
                condition=models.Q(
                    consumed_at__isnull=True,
                    revoked_at__isnull=True,
                ),
                name="uniq_users_open_recovery_per_identity_purpose",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    purpose__in=[
                        "password_reset",
                        "mfa_reset",
                    ],
                ),
                name="ck_users_recovery_token_purpose",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(consumed_at__isnull=True)
                    | models.Q(revoked_at__isnull=True)
                ),
                name="ck_users_recovery_not_consumed_and_revoked",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("created_at")),
                name="ck_users_recovery_expiry_after_issue",
            ),
        ]


class RecoveryOperation(GlobalSecurityRecord):
    class Status(models.TextChoices):
        PENDING_DELIVERY = "pending_delivery"
        DELIVERING = "delivering"
        DELIVERED = "delivered"
        COMPLETED = "completed"
        SUPERSEDED = "superseded"
        EXPIRED = "expired"
        FAILED = "failed"

    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING_DELIVERY,
    )
    delivery_ciphertext = models.BinaryField(null=True)
    delivery_nonce = models.BinaryField(null=True)
    delivery_key_id = models.CharField(max_length=64, blank=True, default="")
    delivery_attempt_id = models.UUIDField(null=True, unique=True)
    delivery_lease_expires_at = models.DateTimeField(null=True)
    delivery_attempt_count = models.PositiveIntegerField(default=0)
    provider_message_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    request_id = models.UUIDField()
    correlation_id = models.UUIDField(db_index=True)
    causal_root_id = models.UUIDField(db_index=True)
    delivered_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "users_recovery_operations"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=["pending_delivery", "delivering"],
                        delivery_ciphertext__isnull=False,
                        delivery_nonce__isnull=False,
                    )
                    & ~models.Q(delivery_key_id="")
                    | models.Q(
                        status__in=[
                            "delivered",
                            "completed",
                            "superseded",
                            "expired",
                            "failed",
                        ],
                        delivery_ciphertext__isnull=True,
                        delivery_nonce__isnull=True,
                        delivery_key_id="",
                    )
                ),
                name="ck_users_recovery_operation_envelope_state",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="completed",
                        delivered_at__isnull=False,
                        completed_at__isnull=False,
                    )
                    | ~models.Q(status="completed")
                    & models.Q(completed_at__isnull=True)
                ),
                name="ck_users_recovery_operation_completion_state",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="delivering",
                        delivery_attempt_id__isnull=False,
                        delivery_lease_expires_at__isnull=False,
                    )
                    | ~models.Q(status="delivering")
                    & models.Q(
                        delivery_attempt_id__isnull=True,
                        delivery_lease_expires_at__isnull=True,
                    )
                ),
                name="ck_users_recovery_operation_lease_state",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status="delivered")
                    | models.Q(delivered_at__isnull=False)
                ),
                name="ck_users_recovery_operation_delivered_state",
            ),
        ]


class TenantGrantSwitchOperation(GlobalSecurityRecord):
    class Status(models.TextChoices):
        PENDING = "pending"
        DB_COMMITTED = "db_committed"
        COMPLETED = "completed"
        FAILED = "failed"

    operation_id = models.UUIDField(unique=True, db_index=True)
    session = models.ForeignKey(IdentitySession, on_delete=models.PROTECT, related_name="switch_operations")
    request_fingerprint = models.CharField(max_length=64)
    expected_current_grant_id = models.UUIDField(null=True)
    expected_current_tenant_id = models.UUIDField(null=True)
    expected_context_version = models.PositiveBigIntegerField(null=True)
    target_tenant_id = models.UUIDField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    result_grant_id = models.UUIDField(null=True)
    result_tenant_id = models.UUIDField(null=True)
    result_context_version = models.PositiveBigIntegerField(null=True)
    expected_refresh_generation = models.PositiveBigIntegerField(null=True)
    expected_refresh_digest = models.CharField(max_length=64, blank=True, default="")
    result_refresh_generation = models.PositiveBigIntegerField(null=True)
    result_refresh_digest = models.CharField(max_length=64, blank=True, default="")
    response_ciphertext = models.BinaryField(null=True)
    response_nonce = models.BinaryField(null=True)
    response_key_id = models.CharField(max_length=64, blank=True, default="")
    response_expires_at = models.DateTimeField(null=True, db_index=True)
    failure_code = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "users_tenant_grant_switch_operations"


class SessionReconciliationRecord(GlobalSecurityRecord):
    class Kind(models.TextChoices):
        REFRESH_ROTATION = "refresh_rotation"
        TENANT_SWITCH = "tenant_switch"
        SESSION_FAMILY_CLEANUP = "session_family_cleanup"

    session = models.ForeignKey(IdentitySession, on_delete=models.PROTECT, related_name="reconciliation_records")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    operation_id = models.UUIDField(db_index=True)
    switch_operation = models.OneToOneField(
        TenantGrantSwitchOperation,
        null=True,
        to_field="operation_id",
        db_column="switch_operation_id",
        on_delete=models.PROTECT,
        related_name="reconciliation_record",
    )
    expected_db_state = models.JSONField()
    expected_redis_state = models.JSONField()
    failure_reason = models.CharField(max_length=128)
    attempt_count = models.PositiveIntegerField(default=0)
    resolved_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "users_session_reconciliation_records"
        constraints = [
            models.UniqueConstraint(
                fields=["session", "kind", "operation_id"],
                name="uniq_users_session_reconciliation_kind_operation",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        kind="tenant_switch",
                        switch_operation__isnull=False,
                        operation_id=models.F("switch_operation_id"),
                    )
                    | models.Q(
                        kind__in=[
                            "refresh_rotation",
                            "session_family_cleanup",
                        ],
                        switch_operation__isnull=True,
                    )
                ),
                name="ck_users_reconciliation_switch_binding",
            ),
        ]


class IdentitySecurityAuditRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    identity_id = models.UUIDField(null=True, db_index=True)
    session_id = models.UUIDField(null=True, db_index=True)
    event_type = models.CharField(max_length=128, db_index=True)
    actor_type = models.CharField(max_length=32)
    actor_ref = models.CharField(max_length=128)
    reason_code = models.CharField(max_length=64)
    subject_hmac = models.CharField(max_length=64, blank=True, default="")
    request_id = models.UUIDField()
    correlation_id = models.UUIDField(db_index=True)
    metadata = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(db_index=True)
    is_legal_hold = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "users_identity_security_audit"
```

`expected_db_state` and `expected_redis_state` are not arbitrary diagnostic
bags. `SessionReconciliationRepository.create(session_id, proof)` accepts one of three frozen,
schema-versioned proof dataclasses keyed by `Kind`, serializes only UUIDs,
integer generations, keyed digests and closed state codes, and validates the
same shape again before worker use. Unknown/missing keys fail closed. A DB
constraint trigger also proves that a non-null switch operation belongs to the
same `IdentitySession` as the reconciliation row; generic code cannot attach a
different session's operation.

`RecoveryToken.operation_id` is write-once. The recovery repository rejects
any attempt to rebind it, and the database audit/integrity trigger rejects a
changed operation FK after insert. `RecoveryOperation` may outlive delivery
and token consumption; `on_delete=PROTECT` prevents retention from severing
the evidence chain. A deferred constraint trigger rejects a newly committed
deliverable `RecoveryOperation` that has no reverse OneToOne token, while
allowing the service's required operation-then-token inserts to commit inside
their single transaction. The operation carries no duplicate Identity, purpose or
expiry authority; all three are read from its linked token. Its immutable
`provider_message_id` is the stable provider idempotency key used by every
lease retry.

Migration `0003_identity_expand.py` installs
`trg_users_identity_audit_append_only`. It rejects every `DELETE` and every
payload `UPDATE`. The only intentional exception is the monotonic legal-hold
latch `is_legal_hold: FALSE -> TRUE` while all other columns remain byte-for-
byte unchanged; clearing the latch or combining it with another mutation is
rejected. This exception is explicit because legal hold must be attachable
after evidence is written without making audit payload mutable.

Use tenant models with exact constraints:

```python
class TenantMembership(BaseEnterpriseModel):
    class Status(models.TextChoices):
        DRAFT = "draft"
        INVITED = "invited"
        ACTIVE = "active"
        DISABLED = "disabled"

    identity_id = models.UUIDField(db_index=True)
    legacy_actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="legacy_actor_memberships",
        null=True,
    )
    display_name = models.CharField(max_length=255)
    position = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, db_index=True)
    authz_version = models.PositiveBigIntegerField(default=1)

    class Meta:
        db_table = "users_tenant_memberships"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "identity_id"],
                condition=models.Q(is_deleted=False),
                name="uniq_users_active_membership",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "legacy_actor_user"],
                condition=models.Q(is_deleted=False),
                name="uniq_users_active_membership_actor",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["draft", "invited", "active", "disabled"],
                ),
                name="ck_users_membership_status",
            ),
        ]


class TenantAuthorizationState(BaseEnterpriseModel):
    policy_version = models.PositiveBigIntegerField(default=1)

    class Meta:
        db_table = "users_tenant_authorization_state"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id"],
                name="uniq_users_tenant_auth_state",
            ),
            models.CheckConstraint(
                condition=models.Q(is_deleted=False),
                name="ck_users_tenant_auth_state_not_soft_deleted",
            ),
        ]


class TenantSessionGrant(BaseEnterpriseModel):
    session = models.ForeignKey(
        "users.IdentitySession",
        on_delete=models.PROTECT,
        related_name="tenant_grants",
    )
    membership = models.ForeignKey(
        TenantMembership,
        on_delete=models.PROTECT,
        related_name="session_grants",
    )
    context_version = models.PositiveBigIntegerField()
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    superseded_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "users_tenant_session_grants"
        constraints = [
            models.UniqueConstraint(
                fields=["session"],
                condition=models.Q(superseded_at__isnull=True, revoked_at__isnull=True),
                name="uniq_users_open_grant_per_session",
            ),
            models.CheckConstraint(
                condition=models.Q(is_deleted=False),
                name="ck_users_grant_not_soft_deleted",
            ),
        ]
```

- [x] **Step 5: Create and inspect the expand migration**

```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py makemigrations users --name identity_expand
docker compose -f docker-compose.dev.yml exec backend python manage.py sqlmigrate users 0003
```

Edit the generated migration so the four new columns on the active
`users_users` table (`identity_id`, `normalized_email`, `security_status` and
`identity_legal_hold`) are nullable and have no database default or implicit
`db_index`; `identity_id` and
`normalized_email` also have neither `UNIQUE` nor UUID defaults. Release N
must not build an ordinary blocking index on `users_users`. The two unique
indexes and the two operational lookup indexes are owned by Task 4 and are
built concurrently only after backfill. Keep
`ck_users_grant_not_soft_deleted` and
`ck_users_tenant_auth_state_not_soft_deleted` in the expand migration so
neither ORM `.update(is_deleted=True)` nor a generic soft-delete repository
can hide security authority. Keep the partial unique TOTP constraint in the
same expand migration. Also keep the partial
`uniq_users_open_recovery_per_identity_purpose` constraint: `start_recovery()`
must explicitly revoke the previous open token before it inserts another one,
including when the previous token has expired.

Install `trg_users_grant_append_only`, a `BEFORE UPDATE` PostgreSQL trigger on
`users_tenant_session_grants`. It raises an integrity exception containing
`grant_append_only` when any
column other than `superseded_at`, `revoked_at`, `version` or `updated_at`
changes. For the two lifecycle timestamps it permits only `NULL -> timestamp`
and never clearing/replacing a value; `version` must increase exactly once per
CAS transition. This database boundary covers raw SQL and `.update()`, not
only the specialized repository.

Install `trg_users_identity_audit_append_only` as `BEFORE UPDATE OR DELETE`.
It raises `identity_audit_append_only` for every delete and every update except
an isolated `is_legal_hold FALSE -> TRUE`; that exception compares every other
column with `IS NOT DISTINCT FROM` and rejects combined changes.

Install `trg_users_identity_merge_operation_guard` as
`BEFORE INSERT OR UPDATE OR DELETE`. Insert accepts only
`APPROVED_PENDING_CONVERGENCE` with `credential_epoch=0`, null convergence
mode/timestamps and null `finalized_at`; every other initial state raises
`identity_merge_invalid_initial_state`. Update rejects authority-field
rewrites and every state edge except pending to deferred/cancelled/expired,
deferred to ready and ready to finalized; every other edge raises
`identity_merge_invalid_transition`. Delete is always rejected, and every
ordinary update is rejected when either old or new `is_legal_hold` is true. It permits
`convergence_mode` only during the single
`APPROVED_PENDING_CONVERGENCE -> APPROVED_DEFERRED` CAS and requires that CAS
to set `credential_epoch=1` plus `credentials_converged_at`. The separate
legal-hold governance function may change only the operation hold
flag/reference/timestamps and cannot combine that change with state or payload
mutation. Install append-only update/delete triggers on merge hold blocks,
requeues and requeue results. Install the deferred
`trg_users_merge_requeue_matches_hold` integrity trigger for operation/action
binding and issue-before-expiry ordering; signature/build/key validity remains
Task 2 service logic.

No `0003` constraint references `users_users.identity_id`. In particular,
`users_identity_merge_operations.source_identity_id` and
`canonical_identity_id` are ordinary UUID columns; repository validation is
the temporary authority until Task 4 first creates
`UNIQUE(users_users.identity_id)`.

Place a reversible data guard first in the `0003` reverse execution order. It
uses only the historical `apps_registry` and supplied schema-editor alias. If
any operation is `APPROVED_DEFERRED`, `READY_TO_FINALIZE` or `FINALIZED`, it
raises `IrreversibleError` before any trigger, table or column is dropped.
`APPROVED_PENDING_CONVERGENCE`, `CANCELLED` and `EXPIRED` change no authority
and therefore do not prevent the clean schema round-trip; pending approvals
are discarded only as part of an explicitly chosen full Release N rollback.

Install `trg_users_recovery_token_operation_immutable` and the deferred
constraint trigger `trg_users_recovery_operation_requires_token`. The first
rejects changing a token's operation FK. The second runs at commit for a new
operation or token deletion and requires either a reverse token with that
operation ID or both rows to have been purged in the same retention
transaction. Install
`trg_users_reconciliation_switch_session_match` to reject a linked switch
operation whose `session_id` differs from the reconciliation row. Trigger
functions raise stable marker codes asserted by the schema tests.
The Task 1 `identity_merge_operation_factory.advance_to(state)` always inserts
the required pending initial row and reaches a requested non-pending fixture
state only through this allowed transition matrix; it never disables the
trigger or inserts a forged realized state.

Install `trg_users_membership_actor_immutable` as `BEFORE UPDATE OF
legacy_actor_user_id`. During expand it permits exactly one `NULL -> actor`
initialization and rejects clearing or replacing a non-null actor with the
stable marker `membership_actor_immutable`; collision merge may change the
membership's canonical `identity_id` but never its historical actor. Add a raw-ORM
`IntegrityError` test proving that neither generic `.update()` nor a future
repository can rebind the actor.

Add `ENABLE ROW LEVEL SECURITY`,
`FORCE ROW LEVEL SECURITY` and tenant `USING/WITH CHECK` policies for the
three tenant tables using
`NULLIF(current_setting('app.current_tenant_id', true), '')::uuid`.

Every `RunSQL` that installs RLS, a policy, trigger or trigger function in
`0003_identity_expand.py` supplies explicit `reverse_sql`. Reverse operations
drop triggers before their functions and drop policies before disabling RLS;
they use exact object names and dependency order. Automatic `CreateModel` and
`AddField` state reversals remain Django-owned. A missing reverse clause is a
migration-linter failure, not a rollout note.

Expected SQL: every newly added `users_users` column is nullable and has no
persistent `DEFAULT`; no `UNIQUE`, `NOT NULL` or `CREATE INDEX` is added to
the active table.

- [x] **Step 6: Run schema and migration checks**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_identity_expand_schema.py -v
docker compose -f docker-compose.dev.yml run --rm --no-deps -v "$PWD:/workspace:ro" -w /workspace --entrypoint python backend infra/scripts/migration_linter.py
git diff --check
```

Expected for IAM: the linter output contains no `backend/apps/users/migrations/0003_identity_expand.py` failure. Current repository baseline separately reports pre-existing `backend/apps/requests/migrations/0002_add_resource_types.py`; do not edit or stage that unrelated migration in this task. The repository-wide merge gate must be green or explicitly resolved by its owner before final integration.

- [ ] **Step 7: Commit Release N expand**

```bash
git add backend/apps/users/models.py backend/apps/users/models_identity.py backend/apps/users/models_membership.py backend/apps/users/migrations/0003_identity_expand.py backend/apps/users/security/iam_retirement_barrier.py backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py backend/apps/users/tests/test_identity_expand_schema.py
git commit -m "feat(iam): add nullable identity foundation"
```

---

### Task 2: Email Normalization and Protected Collision Report

**Files:**

- Create: `backend/apps/users/services/email_normalization.py`
- Create: `backend/apps/users/services/identity_foundation.py`
- Create: `backend/apps/users/services/identity_collision_resolution.py`
- Create: `backend/apps/users/services/pii_access_audit_service.py`
- Create: `backend/apps/users/repositories/__init__.py`
- Create: `backend/apps/users/repositories/membership_repository.py`
- Create: `backend/apps/users/repositories/iam_tenant_registry_repository.py`
- Create: `backend/apps/users/repositories/identity_collision_repository.py`
- Create: `backend/apps/users/repositories/identity_merge_repository.py`
- Create: `backend/apps/users/services/deferred_merge_guard.py`
- Create: `backend/apps/users/services/deferred_merge_credential_synchronizer.py`
- Create: `backend/apps/users/services/merge_convergence_service.py`
- Create: `backend/apps/users/security/deferred_merge_execution_policy.py`
- Create: `backend/apps/users/security/credential_entrypoints.py`
- Create: `backend/apps/users/security/credential_entrypoint_manifest.json`
- Create: `backend/apps/users/security/identity_merge_locking.py`
- Create: `backend/apps/common/contracts/iam_tenant_inventory.py`
- Create: `backend/apps/users/infrastructure/iam_tenant_inventory.py`
- Create: `backend/apps/users/security/reconciliation_approval.py`
- Create: `backend/apps/users/management/commands/report_identity_email_duplicates.py`
- Create: `backend/apps/users/management/commands/resolve_identity_collision.py`
- Create: `backend/apps/users/tasks/__init__.py`
- Create: `backend/apps/users/tasks/identity_merge_maintenance.py`
- Create: `backend/apps/users/management/commands/expire_pending_identity_merges.py`
- Create: `backend/apps/users/management/commands/requeue_identity_merge.py`
- Create: `backend/apps/users/tasks/identity_merge_requeue.py`
- Modify: `backend/apps/users/services/auth_service.py`
- Modify: `backend/apps/users/services/mfa_service.py`
- Modify: `backend/apps/users/services/user_admin_service.py`
- Modify: `backend/apps/users/api.py`
- Modify: `backend/apps/users/schemas/auth.py`
- Modify: `backend/apps/common/api/auth.py`
- Modify: `backend/config/settings/base.py`
- Modify: `backend/config/settings/prod.py`
- Modify: `.env.example`
- Test: `backend/apps/users/tests/test_email_reconciliation.py`
- Test: `backend/apps/users/tests/test_iam_tenant_inventory.py`
- Test: `backend/apps/users/tests/test_deferred_merge_convergence.py`
- Test: `backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py`
- Test: `backend/apps/users/tests/test_identity_merge_locking.py`
- Test: `backend/apps/users/tests/test_identity_merge_requeue.py`

**Interfaces:**

- Produces `normalize_email(email: str) -> NormalizedEmail`.
- Produces `build_collision_groups(queryset) -> list[EmailCollisionGroup]` from current email candidates, not from an already-unique column.
- Produces closed `IdentityCollisionResolutionErrorCode` and
  `IdentityCollisionResolutionError`. At minimum the closed codes include
  `MERGE_OPERATION_ACTIVE`, `APPROVAL_CONTEXT_STALE`, `LEGAL_HOLD_ACTIVE` and
  `MERGE_GRAPH_CHANGED_RETRY`; repositories/services raise the typed error and
  the management command maps only its sanitized `.code` to `CommandError`.
- Produces `report_identity_email_duplicates --output
  /protected/path/report.json --fail-on-duplicates
  --fail-on-invalid-candidates --actor-type operator --actor-ref <operator>
  --request-id <uuid> --correlation-id <uuid>`.
- Produces `PiiAccessAuditService.append_batch_and_commit(...)`; callers must
  durably audit exact non-PII Identity IDs before executing an email-bearing
  query. Audit persistence failure is fail-closed and no report is written.
  The service converts database/serialization failures to the closed
  `PiiAuditUnavailable` exception (without leaking the underlying message);
  command/API adapters map only that typed failure.
- Produces Task 2's `IamTenantInventoryProvider` Protocol,
  `DurableIamTenantInventoryProvider` over the Task 1 registry and separate
  `LegacyIamTenantDiscovery` over the bounded Release N compatibility union.
  Collision merge uses discovery only to prove footprints and registers every
  relevant tenant before a tenant write; Tasks 4/14/17 use only the durable
  provider.
- Produces idempotent
  `IamTenantRegistryRepository.ensure_registered(tenant_id, source_code)`;
  it performs insert-on-conflict-do-nothing and then returns that exact row
  under `SELECT ... FOR UPDATE`. Callers sort all tenant UUIDs and complete
  every registry ensure/lock before their first tenant-table lock; registry
  inserts and tenant-scoped IAM writes share one transaction.
- Produces `resolve_identity_collision --merge ...` and
  `resolve_identity_collision --correct-email ...` with mandatory dry-run,
  CAS, protected approval reference and pseudonymized audit. Approval creates
  `APPROVED_PENDING_CONVERGENCE` only; it does not move membership or mutate
  email/credentials. Correction fails with `MERGE_OPERATION_ACTIVE` while its
  target participates in a live pending merge; only a separately signed cancellation
  followed by a newly signed correction may proceed, subject to every other
  correction-eligibility guard. Realized deferred/ready/finalized source rows
  remain ineligible compatibility rows. Its signed envelope includes the
  expected target security version and post-lock participant-operation digest;
  a pre-cancel signature is stale after cancellation.
- Produces signed `resolve_identity_collision --cancel-merge OPERATION_ID` and
  idempotent retention-queue `expire_pending_identity_merges --batch-size
  500`. Both can only CAS a still-pending operation to terminal `CANCELLED` or
  `EXPIRED`; they change no Identity, membership, credential or session state
  and release only the partial source/synthetic reservation.
- Produces authenticated additive endpoints
  `POST /api/v1/auth/merge-convergence/start` and
  `POST /api/v1/auth/merge-convergence/complete`. Completion is idempotent,
  requires current source authentication plus canonical MFA proof for an
  active graph, and returns no session; the owner performs a fresh login after
  every prior source/canonical artifact is revoked.
- Produces `DeferredMergeGuard.resolve_legacy_actor(...) -> CredentialAuthority`
  and applies it before password comparison, MFA verification, Redis refresh
  lookup, access-JWT acceptance, recovery, status/device checks and session
  issue/revoke.
- Produces `DeferredMergeGuard.require_activation_allowed(identity_id)`. If a
  connected operation is `APPROVED_DEFERRED` or `READY_TO_FINALIZE` with
  `convergence_mode=DORMANT_FAIL_CLOSED`, every transition to canonical
  `ACTIVE` or tenant membership `ACTIVE` fails with the typed
  `MERGE_DORMANT_FINALIZATION_REQUIRED` result. Such a graph must be finalized
  while inactive at the Task 16 cutoff; only a `FINALIZED` graph may enter the
  ordinary canonical onboarding/MFA activation flow afterward.
- Produces `DeferredMergeExecutionPolicy.require_enabled()`; the boolean flag
  is effective only with a valid runtime-signed coverage attestation for the
  exact manifest and build commit.
- Produces one global `IdentityMergeLockCoordinator` shared by approval,
  convergence, canonical credential mutation, finalization and legal-hold
  latch/release.
- Produces focused `IdentityCollisionRepository`; it may mutate only the
  locked collision rows and membership/policy foundation needed by an
  approved resolution. It may lock/read current `RoleBinding` authority but
  never rebind it from the tenant-local legacy actor to the canonical global
  Identity.
- Produces `ensure_identity_foundation(user: User) -> IdentityFoundation`, the
  single locked-row helper used by both reconciliation modes and Task 3
  backfill to initialize nullable expand fields before any CAS mutation.
- Produces the initial idempotent
  `MembershipRepository.ensure_foundation(...)` and
  `ensure_authorization_state(tenant_id)` used by collision merge; Task 3
  extends the same repository for batched backfill rather than duplicating it.
  Membership creation requires `legacy_actor_user_id`; an expand-era null may
  be initialized exactly once but an existing non-null actor never changes.
- Produces a closed credential-entry-point registry. At Release N its required
  runtime entries are legacy login/password verification, common access-JWT
  validation, refresh validation/rotation, logout, MFA pending/setup/confirm/
  verify and tenant-local enable/disable. Password change/admin reset, global
  lock/unlock, logout-all and recovery are recorded as
  `NOT_PRESENT_AT_RELEASE_N`; the task that introduces any one of them must
  register and guard it in the same commit.

- [x] **Step 1: Write failing normalization/report tests**

```python
import json
import stat
import uuid

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command

from apps.common.models.rbac import RoleBinding
from apps.common.tenancy.context import clear_tenant_context, set_tenant_context
from apps.users.models import (
    IdentityMergeHoldBlock,
    IdentityMergeOperation,
    IdentitySecurityAuditRecord,
    TenantMembership,
    User,
)
from apps.users.repositories.identity_collision_repository import (
    IdentityCollisionRepository,
)
from apps.users.repositories.membership_repository import MembershipRepository
from apps.users.services.auth_service import _pk_to_uuid4
from apps.users.services.email_normalization import normalize_email
from apps.users.services.identity_collision_resolution import (
    IdentityCollisionResolutionError,
    IdentityCollisionResolutionErrorCode,
)
from apps.users.services.pii_access_audit_service import (
    PiiAccessAuditService,
    PiiAuditUnavailable,
)


def report_audit_args() -> list[str]:
    return [
        "--actor-type",
        "operator",
        "--actor-ref",
        "iam-migration-operator",
        "--request-id",
        str(uuid.uuid4()),
        "--correlation-id",
        str(uuid.uuid4()),
    ]


@pytest.fixture
def protected_report_dir(tmp_path):
    path = tmp_path / "protected-reconciliation"
    path.mkdir(mode=0o700)
    return path


def test_normalization_v1_is_explicit_and_stable():
    value = normalize_email(" Ivan.Example@EXAMPLE.COM ")
    assert value.value == "ivan.example@example.com"
    assert value.version == 1


def test_normalization_v1_applies_unicode_nfkc_before_casefold():
    composed = normalize_email("CAFÉ@EXAMPLE.COM")
    decomposed = normalize_email("CAFE\u0301@example.com")
    compatibility = normalize_email("ｉｖａｎ＠ＥＸＡＭＰＬＥ．ＣＯＭ")
    unicode_domain = normalize_email("IVAN@ПРИМЕР.РФ")

    assert composed.value == decomposed.value == "café@example.com"
    assert compatibility.value == "ivan@example.com"
    assert unicode_domain.value == "ivan@пример.рф"


@pytest.mark.parametrize(
    "raw",
    [
        "   ",
        "missing-at.example.com",
        f"{'a' * 245}@example.com",
    ],
)
def test_normalization_rejects_invalid_or_overlong_email(raw):
    with pytest.raises(ValidationError):
        normalize_email(raw)


@pytest.mark.django_db
def test_report_finds_candidates_before_unique_constraint(protected_report_dir):
    User.objects.create(
        username="one",
        email="Ivan@Example.com",
        identity_id=uuid.uuid4(),
        normalized_email="legacy-one@example.invalid",
        email_normalization_version=1,
        security_version=1,
        security_status=User.SecurityStatus.ACTIVE,
    )
    User.objects.create(
        username="two",
        email="ivan@example.com",
        identity_id=uuid.uuid4(),
        normalized_email="legacy-two@example.invalid",
        email_normalization_version=1,
        security_version=1,
        security_status=User.SecurityStatus.ACTIVE,
    )
    output = protected_report_dir / "identity-email-collisions.json"

    call_command(
        "report_identity_email_duplicates",
        "--output",
        str(output),
        *report_audit_args(),
    )

    payload = json.loads(output.read_text())
    assert payload["unresolved_duplicate_groups"] == 1
    assert payload["groups"][0]["normalized_email"] == "ivan@example.com"
    assert len(payload["groups"][0]["identities"]) == 2
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    audits = IdentitySecurityAuditRecord.objects.filter(
        event_type="pii.identity_email_read",
        reason_code="IDENTITY_EMAIL_RECONCILIATION_REPORT",
    )
    assert audits.count() == 2
    assert all("@" not in json.dumps(row.metadata) for row in audits)


@pytest.mark.django_db
def test_report_refuses_repository_output():
    with pytest.raises(CommandError, match="outside the repository"):
        call_command(
            "report_identity_email_duplicates",
            "--output",
            str(settings.BASE_DIR / "identity-email-collisions.json"),
            *report_audit_args(),
        )


@pytest.mark.django_db(transaction=True)
def test_report_commits_audit_before_pii_query_and_fails_closed(
    protected_report_dir,
    monkeypatch,
):
    user = User.objects.create(
        username="audited@example.com",
        email="audited@example.com",
    )
    pii_query_called = False

    def load_pii_candidates(*args, **kwargs):
        nonlocal pii_query_called
        pii_query_called = True
        return []

    def fail_audit(*args, **kwargs):
        raise PiiAuditUnavailable("audit unavailable")

    monkeypatch.setattr(
        IdentityCollisionRepository,
        "load_pii_candidates",
        load_pii_candidates,
    )
    monkeypatch.setattr(
        PiiAccessAuditService,
        "append_batch_and_commit",
        fail_audit,
    )
    output = protected_report_dir / "identity-email-collisions.json"

    with pytest.raises(CommandError, match="PII audit unavailable"):
        call_command(
            "report_identity_email_duplicates",
            "--output",
            str(output),
            *report_audit_args(),
        )

    assert user.pk
    assert pii_query_called is False
    assert not output.exists()


@pytest.mark.django_db
def test_report_collects_invalid_identifiers_without_aborting(protected_report_dir):
    User.objects.create(username="not-an-email", email="")
    User.objects.create(username="", email="missing-at.example.com")
    output = protected_report_dir / "identity-email-collisions.json"

    call_command(
        "report_identity_email_duplicates",
        "--output",
        str(output),
        *report_audit_args(),
    )

    payload = json.loads(output.read_text())
    assert payload["invalid_candidates"]["count"] == 2
    assert {row["user_pk"] for row in payload["invalid_candidates"]["rows"]}

    with pytest.raises(CommandError, match="invalid identity candidates"):
        call_command(
            "report_identity_email_duplicates",
            "--output",
            str(protected_report_dir / "identity-email-invalid.json"),
            "--fail-on-invalid-candidates",
            *report_audit_args(),
        )


@pytest.mark.django_db
def test_report_rejects_symlink_target_without_touching_referent(
    protected_report_dir,
):
    referent = protected_report_dir / "operator-owned.json"
    referent.write_text("do-not-overwrite")
    output = protected_report_dir / "identity-email-collisions.json"
    output.symlink_to(referent)

    with pytest.raises(CommandError, match="symlink or non-regular"):
        call_command(
            "report_identity_email_duplicates",
            "--output",
            str(output),
            *report_audit_args(),
        )

    assert referent.read_text() == "do-not-overwrite"


@pytest.mark.django_db(transaction=True)
def test_collision_resolution_requires_dry_run_and_approval_reference(
    collision_pair,
):
    with pytest.raises(CommandError, match="approval-reference"):
        call_command(
            "resolve_identity_collision",
            "--correct-email",
            str(collision_pair.second.pk),
            "--email",
            "corrected@example.com",
            "--execute",
        )

    call_command(
        "resolve_identity_collision",
        "--correct-email",
        str(collision_pair.second.pk),
        "--email",
        "corrected@example.com",
        "--approval-reference",
        "IAM-CHANGE-42",
        "--dry-run",
    )
    collision_pair.second.refresh_from_db()
    assert collision_pair.second.email != "corrected@example.com"


@pytest.mark.parametrize("participant", ["source", "canonical"])
@pytest.mark.django_db(transaction=True)
def test_correct_email_requires_fresh_signature_after_pending_merge_cancellation(
    pending_merge_with_distinct_credentials,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
    participant,
):
    state = pending_merge_with_distinct_credentials
    target = getattr(state, participant)
    original_snapshot = (
        target.email,
        target.normalized_email,
        target.security_version,
        state.operation.state,
        state.operation.version,
    )
    blocked_correction = signed_reconciliation_approval.for_correct_email(
        user_pk=target.pk,
        email="corrected@example.com",
        reference="IAM-CHANGE-44-BLOCKED",
    )

    with pytest.raises(IdentityCollisionResolutionError) as typed_active:
        identity_collision_resolution_service.correct_email(
            user_pk=target.pk,
            new_email="corrected@example.com",
            approval=blocked_correction,
        )
    assert (
        typed_active.value.code
        is IdentityCollisionResolutionErrorCode.MERGE_OPERATION_ACTIVE
    )

    with pytest.raises(CommandError, match="MERGE_OPERATION_ACTIVE"):
        call_command(
            "resolve_identity_collision",
            "--correct-email",
            str(target.pk),
            "--email",
            "corrected@example.com",
            "--approval-reference",
            blocked_correction.reference,
            "--approval-key-id",
            blocked_correction.key_id,
            "--approval-signature",
            blocked_correction.signature,
            "--execute",
        )

    state.refresh()
    target = getattr(state, participant)
    assert (
        target.email,
        target.normalized_email,
        target.security_version,
        state.operation.state,
        state.operation.version,
    ) == original_snapshot

    cancellation = signed_reconciliation_approval.for_cancel_merge(
        operation_id=state.operation.id,
        expected_version=state.operation.version,
        reference="IAM-CHANGE-44-CANCEL",
    )
    call_command(
        "resolve_identity_collision",
        "--cancel-merge",
        str(state.operation.id),
        "--approval-reference",
        cancellation.reference,
        "--approval-key-id",
        cancellation.key_id,
        "--approval-signature",
        cancellation.signature,
        "--execute",
    )

    state.operation.refresh_from_db()
    assert state.operation.state == IdentityMergeOperation.State.CANCELLED
    assert IdentityMergeOperation.objects.filter(id=state.operation.id).count() == 1

    with pytest.raises(IdentityCollisionResolutionError) as typed_stale:
        identity_collision_resolution_service.correct_email(
            user_pk=target.pk,
            new_email="corrected@example.com",
            approval=blocked_correction,
        )
    assert (
        typed_stale.value.code
        is IdentityCollisionResolutionErrorCode.APPROVAL_CONTEXT_STALE
    )

    with pytest.raises(CommandError, match="APPROVAL_CONTEXT_STALE"):
        call_command(
            "resolve_identity_collision",
            "--correct-email",
            str(target.pk),
            "--email",
            "corrected@example.com",
            "--approval-reference",
            blocked_correction.reference,
            "--approval-key-id",
            blocked_correction.key_id,
            "--approval-signature",
            blocked_correction.signature,
            "--execute",
        )

    correction = signed_reconciliation_approval.for_correct_email(
        user_pk=target.pk,
        email="corrected@example.com",
        reference="IAM-CHANGE-44-CORRECT",
    )
    call_command(
        "resolve_identity_collision",
        "--correct-email",
        str(target.pk),
        "--email",
        "corrected@example.com",
        "--approval-reference",
        correction.reference,
        "--approval-key-id",
        correction.key_id,
        "--approval-signature",
        correction.signature,
        "--execute",
    )

    target.refresh_from_db()
    assert target.email == "corrected@example.com"
    assert state.operation.state == IdentityMergeOperation.State.CANCELLED
    pending_merge_count = IdentityMergeOperation.objects.filter(
        id=state.operation.id,
        state=IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE,
    ).count()
    assert pending_merge_count == 0


@pytest.mark.django_db(transaction=True)
def test_signed_approval_creates_pending_operation_without_authority_change(
    collision_pair,
    signed_reconciliation_approval,
):
    source = collision_pair.second
    canonical = collision_pair.first
    before = (source.email, source.normalized_email, source.password, source.is_active)
    approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-CHANGE-43",
    )

    call_command(
        "resolve_identity_collision",
        "--merge",
        str(source.pk),
        "--into",
        str(canonical.pk),
        "--approval-reference",
        approval.reference,
        "--approval-key-id",
        approval.key_id,
        "--approval-signature",
        approval.signature,
        "--execute",
    )

    source.refresh_from_db()
    operation = IdentityMergeOperation.objects.get(source_user=source)
    assert operation.state == IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE
    assert operation.credential_epoch == 0
    assert (source.email, source.normalized_email, source.password, source.is_active) == before
    set_tenant_context(source.tenant_id)
    try:
        membership = TenantMembership.objects.get(legacy_actor_user_id=source.pk)
        assert membership.identity_id == source.identity_id
    finally:
        clear_tenant_context()


@pytest.mark.django_db(transaction=True)
def test_active_owner_convergence_establishes_one_credential_authority(
    pending_merge_with_distinct_credentials,
    valid_runtime_merge_coverage_attestation,
    client,
):
    state = pending_merge_with_distinct_credentials
    source_session = state.login_source_with_old_password_and_mfa()
    started = client.post(
        "/api/v1/auth/merge-convergence/start",
        json={"operation_id": str(state.operation.id)},
        headers=source_session.headers,
    )
    assert started.status_code == 200

    completed = client.post(
        "/api/v1/auth/merge-convergence/complete",
        json={
            "challenge_token": started.json()["challenge_token"],
            "new_password": "One-New-Password-42!",
            "totp_code": state.current_canonical_totp(),
            "idempotency_key": str(uuid.uuid4()),
        },
        headers=source_session.headers,
    )
    assert completed.status_code == 204

    state.refresh()
    assert state.operation.state == IdentityMergeOperation.State.APPROVED_DEFERRED
    assert state.operation.credential_epoch == 1
    assert state.source.password == state.canonical.password
    assert state.source.is_active is False
    assert state.source.normalized_email == state.operation.synthetic_normalized_email
    assert state.membership.identity_id == state.canonical.identity_id
    assert state.membership.legacy_actor_user_id == state.source.pk
    assert state.old_source_password_is_rejected()
    assert state.old_canonical_password_is_rejected()
    assert state.old_source_totp_is_rejected()
    assert state.new_password_and_canonical_totp_work_via_source_alias()
    assert state.pre_completion_access_refresh_and_mfa_partial_are_rejected()


@pytest.mark.django_db(transaction=True)
def test_dormant_convergence_requires_the_complete_fail_closed_profile(
    dormant_pending_merge,
    valid_runtime_merge_coverage_attestation,
):
    operation = dormant_pending_merge.complete_without_owner_credentials()
    assert operation.convergence_mode == "dormant_fail_closed"
    assert operation.state == "approved_deferred"
    assert dormant_pending_merge.all_logins_fail_closed()
    assert dormant_pending_merge.no_live_artifact_or_membership_access()

    mixed = dormant_pending_merge.with_one_active_source_or_membership()
    with pytest.raises(CommandError, match="DORMANT_PROFILE_NOT_SATISFIED"):
        mixed.complete_without_owner_credentials()


@pytest.mark.django_db(transaction=True)
def test_hold_creates_separate_evidence_and_changes_no_held_state(
    held_pending_merge,
):
    before = held_pending_merge.byte_snapshot()
    with pytest.raises(CommandError, match="LEGAL_HOLD_ACTIVE"):
        held_pending_merge.complete()
    assert held_pending_merge.byte_snapshot() == before
    assert IdentityMergeHoldBlock.objects.filter(
        operation=held_pending_merge.operation,
    ).count() == 1


@pytest.mark.django_db(transaction=True)
def test_correct_email_initializes_nullable_expand_fields_before_cas(
    legacy_user_without_identity_foundation,
    signed_reconciliation_approval,
):
    user = legacy_user_without_identity_foundation
    approval = signed_reconciliation_approval.for_correct_email(
        user_pk=user.pk,
        email="corrected@example.com",
        reference="IAM-CHANGE-45",
    )
    call_command(
        "resolve_identity_collision",
        "--correct-email",
        str(user.pk),
        "--email",
        "corrected@example.com",
        "--approval-reference",
        approval.reference,
        "--approval-key-id",
        approval.key_id,
        "--approval-signature",
        approval.signature,
        "--execute",
    )
    user.refresh_from_db()
    assert user.identity_id is not None
    assert user.security_version == 2
    assert user.security_status in set(User.SecurityStatus.values)
    assert user.email_normalization_version == 1
    set_tenant_context(user.tenant_id)
    try:
        membership = TenantMembership.objects.get(
            identity_id=user.identity_id,
            is_deleted=False,
        )
        assert membership.legacy_actor_user_id == user.pk
    finally:
        clear_tenant_context()
```

- [x] **Step 2: Run the tests and verify failure**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest \
  apps/users/tests/test_email_reconciliation.py \
  apps/users/tests/test_iam_tenant_inventory.py \
  apps/users/tests/test_deferred_merge_convergence.py \
  apps/users/tests/test_deferred_merge_entrypoint_coverage.py \
  apps/users/tests/test_identity_merge_locking.py \
  apps/users/tests/test_identity_merge_requeue.py -v
```

Expected: FAIL because normalization, the report/approval commands, convergence
services, merge guard, runtime attestation and lock coordinator do not exist.

- [x] **Step 3: Implement versioned normalization**

```python
import unicodedata
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

EMAIL_NORMALIZATION_VERSION = 1


@dataclass(frozen=True, slots=True)
class NormalizedEmail:
    value: str
    version: int


def normalize_email(email: str) -> NormalizedEmail:
    value = unicodedata.normalize("NFKC", email.strip()).casefold()
    if len(value) > 254:
        raise ValidationError("EMAIL_TOO_LONG")
    validate_email(value)
    return NormalizedEmail(
        value=value,
        version=EMAIL_NORMALIZATION_VERSION,
    )
```

The order is exact: trim, Unicode NFKC normalization, casefold, length check,
email syntax validation. Do not perform provider-specific dot removal or
plus-address stripping.

- [x] **Step 4: Implement candidate grouping and command behavior**

The command first uses
`IdentityCollisionRepository.enumerate_subjects()` to read only `pk` and
nullable `identity_id`. It requires explicit operator/system actor type,
non-empty actor ref, request UUID and correlation UUID, then calls
`PiiAccessAuditService.append_batch_and_commit()` with the fixed reason
`IDENTITY_EMAIL_RECONCILIATION_REPORT`. Only after that independent audit
transaction commits may
`IdentityCollisionRepository.load_pii_candidates(audited_pks)` select
`pk/email/username/tenant_id`. An audit failure becomes `CommandError("PII
audit unavailable")`; the PII query is never issued and no file is created.
The command calculates the candidate from `email or username`, groups valid
values in memory by normalized value, catches `ValidationError` per row and
writes malformed rows to `invalid_candidates`; one malformed legacy row never
aborts the scan. It writes raw reconciliation data only to the explicitly supplied file in a
pre-provisioned protected directory outside the repository. The directory
must already exist, be owned by the service UID, have no group/other
permission bits and resolve without symlink components. The target may be
absent or a regular file, never a symlink or another file type. Application
logs contain only duplicate/invalid counts and a report SHA-256 digest.

```python
import json
import os
import stat
import uuid
from pathlib import Path

from django.conf import settings
from django.core.management import CommandError

from apps.users.repositories.identity_collision_repository import (
    IdentityCollisionRepository,
)
from apps.users.services.identity_foundation import (
    build_collision_groups,
    serialize_groups,
)
from apps.users.services.pii_access_audit_service import (
    PiiAccessAuditService,
    PiiAuditUnavailable,
)

repository = Path(settings.BASE_DIR).resolve()
requested_parent = output_path.parent.absolute()
protected_parent = output_path.parent.resolve(strict=True)
if protected_parent != requested_parent:
    raise CommandError("Protected report directory must not contain symlinks")
if protected_parent.is_relative_to(repository):
    raise CommandError("Reconciliation output must be outside the repository")

dir_fd = os.open(
    protected_parent,
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
)
try:
    directory_stat = os.fstat(dir_fd)
    if (
        directory_stat.st_uid != os.geteuid()
        or stat.S_IMODE(directory_stat.st_mode) & 0o077
    ):
        raise CommandError("Protected report directory must be owner-only")
    try:
        target_stat = os.stat(
            output_path.name,
            dir_fd=dir_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(target_stat.st_mode):
            raise CommandError("Report target is a symlink or non-regular file")

    subjects = IdentityCollisionRepository.enumerate_subjects()
    try:
        PiiAccessAuditService.append_batch_and_commit(
            subjects=subjects,
            actor_type=options["actor_type"],
            actor_ref=options["actor_ref"],
            reason_code="IDENTITY_EMAIL_RECONCILIATION_REPORT",
            request_id=options["request_id"],
            correlation_id=options["correlation_id"],
        )
    except PiiAuditUnavailable as exc:
        raise CommandError("PII audit unavailable") from exc
    candidates = IdentityCollisionRepository.load_pii_candidates(
        [subject.user_pk for subject in subjects],
    )
    groups = build_collision_groups(candidates)
    payload = json.dumps(
        serialize_groups(groups),
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    temp_name = f".{output_path.name}.{uuid.uuid4().hex}.tmp"
    temp_fd = os.open(
        temp_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=dir_fd,
    )
    try:
        with os.fdopen(temp_fd, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temp_name,
            output_path.name,
            src_dir_fd=dir_fd,
            dst_dir_fd=dir_fd,
        )
        os.fsync(dir_fd)
    finally:
        try:
            os.unlink(temp_name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
finally:
    os.close(dir_fd)

logger.info(
    "identity_email_reconciliation_reported",
    duplicate_groups=len(groups),
    report_sha256=file_sha256(output_path),
)
if groups and options["fail_on_duplicates"]:
    raise CommandError(f"{len(groups)} unresolved normalized-email groups")
if invalid_candidates and options["fail_on_invalid_candidates"]:
    raise CommandError(f"{len(invalid_candidates)} invalid identity candidates")
```

Create the Release N inventory boundary in
`apps/common/contracts/iam_tenant_inventory.py`:

```python
from collections.abc import Iterator
from typing import Protocol
from uuid import UUID


class IamTenantInventoryProvider(Protocol):
    def iter_tenant_ids(self) -> Iterator[UUID]: ...
```

`LegacyIamTenantDiscovery` reads primary/global compatibility sources only and
yields the unique non-null union of `User.tenant_id`, `RoleBinding.tenant_id`
and expected/target/result tenant columns of `TenantGrantSwitchOperation`. It
never queries an RLS model globally and is allowed only in Task 2 collision
footprint checks plus Task 3 registry seeding. Before merge writes any tenant
foundation it calls `ensure_registered()` for all participating tenant IDs.
`ensure_registered()` inserts idempotently and then locks the exact registry
row; multi-tenant callers must finish this operation for every UUID-sorted
tenant before entering the first RLS context.

`DurableIamTenantInventoryProvider` reads only append-only
`IamTenantRegistryEntry` and yields UUID-sorted IDs. The focused inventory test
seeds each compatibility source, explicitly registers the discovered IDs and
proves durable inventory equals that union; it also creates a registry-only
tenant with a DRAFT membership and proves the provider includes it. The merge
test records A/B RLS entry and proves no tenant-scoped query occurs outside its
matching context.

- [x] **Step 5: Implement supported collision resolution**

`resolve_identity_collision` has mutually exclusive modes:

```text
--correct-email USER_PK --email EMAIL
--merge SOURCE_USER_PK --into CANONICAL_USER_PK
--cancel-merge OPERATION_ID
```

All modes default to `--dry-run`. Dry-run prints a canonical SHA-256 action digest.
Execute requires `--approval-reference`, `--approval-key-id` and
`--approval-signature`; `ReconciliationApprovalVerifier` verifies Ed25519 over
the canonical action envelope. A free-form ticket is not approval.

`--correct-email` keeps the existing behavior: initialize nullable foundation
under the shared `IdentityMergeLockCoordinator` bootstrap/participant locks,
re-read the target and complete participant merge graph after locking, replace both email
authorities when applicable, update
`normalized_email`, increment `security_version`, ensure the target actor
membership and write only keyed old/new HMACs to audit. Before mutation it
computes a canonical digest of every operation ID/state/version in which the
target participates as source or canonical Identity. The signed action
envelope binds that digest and the target's expected `security_version`.
A live `APPROVED_PENDING_CONVERGENCE` operation returns the typed
`MERGE_OPERATION_ACTIVE` conflict without changing either row. The operator
must execute a separately signed `--cancel-merge OPERATION_ID` first and then
submit a newly signed `--correct-email` action; the correction path never
performs an implicit cancel or accepts the merge/cancellation signature. This
pending check is additional to the existing correction eligibility checks;
deferred/ready/finalized source rows remain ineligible compatibility rows. A
pre-cancellation correction signature binds the prior participant digest and
therefore returns `APPROVAL_CONTEXT_STALE` if replayed after cancellation.

`--merge` is intentionally approval-only and non-converging. Under the global lock
coordinator it validates the same normalized candidate, disjoint tenant actor
footprints, exact source/canonical versions, no chain/cycle, one merge per
source and at most one pending convergence for a canonical Identity. It
reserves
`merged+<source-identity-uuid>@identity.invalid`, verifies that value is absent
from both `User.normalized_email` and
non-cancelled/non-expired
`IdentityMergeOperation.synthetic_normalized_email` reservations, then inserts
a durable, versioned `APPROVED_PENDING_CONVERGENCE` operation with immutable
approval/identity payload. Membership, source/canonical email,
password, MFA, status, session and token state remain byte-for-byte unchanged.
Normal Identity create/update/correct-email rejects the exact reserved domain.

`--cancel-merge` requires a fresh purpose-bound signed approval for the exact
pending operation/version and changes only
`APPROVED_PENDING_CONVERGENCE -> CANCELLED`. The retention task locks pending
rows in bounded batches, and after the full graph/hold check changes only rows
whose immutable `approval_expires_at <= now()` to `EXPIRED`; held operations
remain unchanged and receive separate hold-block evidence. Both terminal
transitions are idempotent and append pseudonymized audit/outbox evidence.
Tests prove byte-for-byte authority state is unchanged and a later separately
signed pending operation can reuse the released deterministic synthetic value.

The collision report treats states exactly:

- a pending approval remains unresolved and makes correction return
  `MERGE_OPERATION_ACTIVE`; it must first reach `CANCELLED` through the
  separately signed command or `EXPIRED` through the explicit retention CAS;
- an expired or cancelled approval leaves the underlying duplicate unresolved
  until a fresh merge approval is completed or a newly signed email correction
  succeeds;
- `APPROVED_DEFERRED` is resolved only when the operation row, source actor,
  canonical Identity, synthetic normalized email and every moved membership
  form the exact relational proof;
- audit metadata is evidence that an event occurred, never runtime mapping
  authority;
- source raw email/username may remain for pre-cutoff compatibility, so the
  report excludes a deferred source from candidate grouping only after the
  relational proof above. A forged audit or stale/incomplete operation still
  blocks Task 4.

#### Owner-confirmed and dormant convergence

`POST /api/v1/auth/merge-convergence/start` requires a fully authenticated
legacy source session, resolves its unique pending operation and creates a
five-minute `PreAuthChallenge(flow_kind=MERGE_CONVERGENCE)` bound to operation
ID, operation version, source/canonical security versions and one idempotency
key. When canonical MFA exists, completion must prove that exact factor. When
it does not exist and the graph is owner-eligible, start creates one encrypted
pending canonical factor and returns its provisioning material once; plaintext
is never persisted or logged.

`POST /api/v1/auth/merge-convergence/complete` accepts only the challenge,
one new 12–128 character password, TOTP code and idempotency key. In one
primary transaction it:

1. takes the complete merge-graph locks and revalidates approval, versions,
   participant set, tenant footprints and all holds;
2. verifies the existing or pending canonical encrypted factor;
3. encodes the password once and writes that exact encoded value to canonical,
   the current source and every non-held already-deferred source shadow;
4. increments canonical `security_version` exactly once, revokes every
   source/canonical session, refresh family, access/MFA-partial challenge,
   backup/recovery artifact and switches legacy MFA verification to the
   canonical encrypted factor;
5. moves each source membership's `identity_id` to canonical while preserving
   immutable `legacy_actor_user_id` and existing `RoleBinding` authority;
6. changes only source `normalized_email` to the reserved synthetic value,
   sets the source compatibility row non-authenticating, sets
   `credential_epoch=1`, `convergence_mode=OWNER_CONFIRMED`,
   `credentials_converged_at` and state `APPROVED_DEFERRED`;
7. inserts pseudonymized audit/outbox evidence after the DB mutations in the
   same transaction.

The endpoint returns `204` and no token/session. The owner logs in again;
source username is then an alias resolved to canonical authority before status,
password, device policy, MFA, recovery or Redis checks. Old source/canonical
passwords, TOTP codes and every pre-completion access/refresh/MFA-partial
artifact fail immediately. Redis cleanup runs only after commit; failure
creates durable cleanup reconciliation, while the DB version/epoch guard
already rejects stale artifacts.

The separate `DORMANT_FAIL_CLOSED` completion is an authenticated operator
command with the same signed approval and runtime attestation. It is allowed
only when canonical and every source have `is_active=False` and status exactly
`LOCKED`, `PENDING_CREDENTIALS` or `MFA_SETUP_REQUIRED`, all involved
memberships are `DISABLED` or `DRAFT`, and there are zero open grants,
sessions, refresh families,
challenges and recovery operations. It moves membership/synthetic normalized
email, sets epoch `1` and keeps canonical locked without creating/changing
password or MFA. An active source, active membership or mixed graph rejects
this mode; use owner convergence, owner recovery, or separately sign and
execute `--cancel-merge` before obtaining a new signature for
`--correct-email`.

#### Runtime-enforced coverage attestation

Production configuration contains:

```text
IAM_DEFERRED_MERGE_EXECUTION_ENABLED
IAM_DEFERRED_MERGE_COVERAGE_DIGEST
IAM_DEFERRED_MERGE_COVERAGE_KEY_ID
IAM_DEFERRED_MERGE_COVERAGE_SIGNATURE
IAM_DEFERRED_MERGE_COVERAGE_COMMIT
IAM_DEFERRED_MERGE_COVERAGE_APPROVED_AT
IAM_DEFERRED_MERGE_COVERAGE_EXPIRES_AT
```

The signed canonical envelope contains purpose/domain separation, environment,
schema/scanner version, SHA-256 of the sorted credential-entry-point manifest,
exact build commit, approval time, nonce and expiry no later than seven days
after approval. Runtime rejects cross-environment evidence and verifies Ed25519 with
the configured current/previous public-key ring, recomputes the registered
manifest digest, compares the immutable image build commit and rejects expiry.
CI's static scanner rejects direct password/hash, MFA secret, JWT decode,
refresh/session or global status paths absent from that manifest. Commit
binding makes a newly added unscanned entry point invalidate the attestation.

Startup computes `effective_merge_execution = boolean && attestation_valid`.
Invalid evidence keeps the application available but forces effective false,
emits one reason-coded metric/log and makes `check --deploy` non-zero when the
boolean was requested. The shared policy re-verifies immediately before owner
completion, dormant completion, the pending-to-deferred convergence CAS and
each `APPROVED_DEFERRED -> READY_TO_FINALIZE -> FINALIZED` finalization CAS;
checking only at startup is insufficient.
The approval-only `--execute --merge` stores a signed pending operation and
changes no authority, so report, dry-run and that pending-approval storage
remain available while effective execution is false.

The Release N manifest registers the reachable legacy paths in
`auth_service.login`, `refresh_access_token`, `logout`, common `JWTAuth`, and
MFA setup/confirm/pending verification plus tenant enable/disable. Guard
resolution occurs before password/MFA comparison and before Redis lookup.
Future password/admin reset, recovery, global lock/unlock and logout-all
services cannot be enabled until their task updates the registry, scanner
evidence, behavioral tests and attestation.
Any later task that adds, removes or moves one of these entry points owns the
registry file, canonical manifest, coverage regression and release evidence in
the same commit. That artifact may deploy with effective merge execution
false, but convergence/finalization cannot be re-enabled until a fresh
signature is issued for that exact build/environment/digest; no earlier task's
attestation is reusable.

#### Closed post-convergence security-mutation matrix

After an operation reaches `APPROVED_DEFERRED`, the canonical Identity is the
only credential, status and recovery authority. Every existing and future
security mutation enters through the registered manifest, takes the complete
merge-graph lock and applies only the effects in this closed matrix:

| Mutation | Canonical Identity | Every deferred source compatibility row |
|---|---|---|
| `PasswordHashChanged` | Encode once, store the new hash and increment `security_version` | Copy that exact encoded hash as a non-authoritative compatibility shadow; never encode independently |
| `MfaConfirmed` | Persist the encrypted canonical factor with `key_id`, confirm it and increment `security_version` | Point legacy verification through the guard to the canonical factor; never copy or persist plaintext |
| `MfaReset` | Revoke canonical factors, set the required security status and increment `security_version` | Increment `credential_epoch`/invalidate the alias guard and legacy MFA artifacts; do not mutate legacy plaintext fields |
| `GlobalStatusChanged` | Apply the typed status transition and increment `security_version` | Update only the fail-closed guard snapshot/epoch used by legacy paths; do not create a second status authority |
| `LogoutAll` | Increment the session/security revocation version and revoke all graph sessions/families | Increment `credential_epoch` and revoke alias/session artifacts; do not modify password or MFA fields |

No generic synchronizer may update columns outside the selected row in this
matrix. Recovery completion is `PasswordHashChanged + LogoutAll`; it does not
invent a sixth effect. A held operation or participant blocks the entire graph
mutation and writes only `IdentityMergeHoldBlock` evidence.

#### Global lock order and legal hold

`IdentityMergeLockCoordinator` first performs a read-only fixed-point discovery
of the request source/canonical and every connected operation except terminal
pre-convergence `CANCELLED`/`EXPIRED`. Realized `FINALIZED` edges remain in the
participant/hold-integrity graph while their source credentials are no longer
alias authority after Task 16.
It takes transaction advisory locks first for sorted User-PK bootstrap keys
and then for sorted Identity UUID bytes, then locks all participant `User` rows
with one `ORDER BY pk` query and all merge operations `ORDER BY id`. It then
locks existing merge evidence by the closed rank
`IdentityMergeHoldBlock`, `IdentityMergeRequeue`,
`IdentityMergeRequeueResult`, each by PK. It next locks global rows by this
closed table rank and PK: `IamTenantRegistryEntry`, `MfaCredential`,
`BackupCode`, `RecoveryOperation`, `RecoveryToken`,
`PreAuthChallenge`, `IdentitySession`, `TenantGrantSwitchOperation`,
`SessionReconciliationRecord`; then, per tenant ordered by tenant UUID,
the exact tenant rank is `TenantAuthorizationState`, `TenantMembership`,
`RoleBinding`, `TenantSessionGrant`. Any flow touching more than one rank uses
this coordinator; no validator/session/security service may acquire the
inverse order, and a batch materializes all tenant intents before its first
tenant write.

After locks, the coordinator repeats fixed-point graph discovery and rechecks
chains, cycles, participant versions, operation states and holds. A newly
observed participant yields typed `MERGE_GRAPH_CHANGED_RETRY`; it never takes
an additional lower-order lock. Advisory locks reduce graph races but never
replace row locks. A concurrent second pending operation for one canonical
returns `CANONICAL_CONVERGENCE_BUSY`; after the first completes, a later source
may converge in a new ceremony and its password mutation synchronizes every
already-deferred alias shadow. All aliases share the canonical rate-limit
bucket.

Every writer that can create a membership, actor mapping or `RoleBinding` for
a participating Identity first takes the same transaction-scoped participant
advisory key before tenant-row locks. This includes Task 2/3 foundation and
ordinary user administration; otherwise a phantom tenant footprint could be
inserted between graph discovery and CAS. Target row locks never use
`skip_locked`. After the sorted locks are materialized, the action digest,
tenant footprint and hold set are recomputed before any foundation write,
audit or outbox insert. `ensure_identity_foundation()` may change only
`identity_legal_hold NULL -> FALSE`; it never overwrites `TRUE`, and any held
participant aborts before that helper is called.

If the operation itself is held, every operation update is forbidden. If a
source/canonical/related row is held, that subject and the operation remain
unchanged. Either case inserts only idempotent append-only
`IdentityMergeHoldBlock`. After authorized hold release, a signed
`IdentityMergeRequeue` references the exact block/release evidence. The worker
locks the graph, rechecks every hold and inserts one immutable OneToOne result
in the same transaction as any resumed CAS. No row resumes merely because a
hold flag disappeared.

`requeue_identity_merge --hold-block ID --dry-run/--execute` owns creation of
the signed `IdentityMergeRequeue`; its canonical envelope is purpose- and
environment-separated and binds operation/block IDs, prior and fresh action
digests, schema version, deployed build, nonce, issue/expiry, key ID, request
and correlation IDs. The retention-queue worker in
`identity_merge_requeue.py` first reads only a bounded UUID-ordered candidate-
ID page. For each candidate it discovers the complete graph, takes User/
operation locks through the coordinator, then takes a non-blocking transaction
advisory work key and locks the requeue/evidence rows at their declared rank.
A busy candidate is counted and rescheduled; it is never
`select_for_update(skip_locked)` before User. Under those same locks it
revalidates signature/key validity, build, expiry, expected operation version
and the entire graph/hold set. It never updates the append-only requeue row. It
inserts exactly one immutable
`IdentityMergeRequeueResult` with `APPLIED`, `HOLD_STILL_ACTIVE`,
`VERSION_CONFLICT` or `INVALID_RELEASE_EVIDENCE`; `APPLIED` is committed with
the resumed CAS/audit/outbox, while delivery remains `on_commit`. A crash
before commit leaves no result and the row is retryable. Identical command
replay returns the existing row/result; reused idempotency ID with a different
canonical payload fails typed. Focused tests cover all four outcomes,
expired/revoked/wrong-build signatures, crash-before-commit, one outbox and
byte-for-byte unchanged held aggregates. A real two-connection test races a
requeue worker against password reset/finalization on the same graph and
requires one serialized result or typed retry, never a database deadlock.

After the first `APPROVED_DEFERRED` commit, startup refuses any build without
the Task 2 guard/registry, and reversing `0003` refuses while deferred/ready/
finalized rows exist. Rollback remains possible only to merge-aware `LEGACY`
mode on Task 2 plus `0003`; pre-Task-2 application/schema rollback is no longer
safe. Before the first completion, ordinary Release N rollback remains fully
reversible.

The focused tests are mandatory, not illustrative:

- source and canonical start with different passwords and TOTP factors;
  pending approval leaves both untouched, while owner completion makes old
  passwords/TOTP and all prior access, refresh and MFA-partial artifacts fail;
- each row of the mutation matrix asserts the exact changed and unchanged
  columns, including canonical password reset and MFA reset through a source
  alias;
- an invalid, expired, wrong-commit, wrong-manifest or unknown-key coverage
  attestation makes effective execution false even when the boolean flag is
  true; adding an unregistered entry point fails the CI scanner;
- `A -> B` racing `B -> A`, `A -> C` racing `B -> C`, canonical password reset
  racing source addition, and finalizer racing a hold latch return one
  serialized success plus a typed conflict/retry; PostgreSQL deadlock is never
  an accepted outcome. The tests use separate PostgreSQL connections,
  barriers and bounded `lock_timeout`, assert no SQLSTATE `40P01`, and never
  substitute SQLite or one sequential transaction;
- after the first deferred completion, a pre-Task-2 application startup and
  reverse of `0003` fail closed, while merge-aware `LEGACY` mode remains a
  supported rollback floor.

- [x] **Step 6: Run the report and resolution tests**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest \
  apps/users/tests/test_email_reconciliation.py \
  apps/users/tests/test_iam_tenant_inventory.py \
  apps/users/tests/test_deferred_merge_convergence.py \
  apps/users/tests/test_deferred_merge_entrypoint_coverage.py \
  apps/users/tests/test_identity_merge_locking.py \
  apps/users/tests/test_identity_merge_requeue.py -v
git diff --check
```

Expected: PASS; no report artifact exists inside the repository.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/users/services/email_normalization.py backend/apps/users/services/identity_foundation.py backend/apps/users/services/identity_collision_resolution.py backend/apps/users/services/pii_access_audit_service.py backend/apps/users/repositories/__init__.py backend/apps/users/repositories/membership_repository.py backend/apps/users/repositories/iam_tenant_registry_repository.py backend/apps/users/repositories/identity_collision_repository.py backend/apps/users/repositories/identity_merge_repository.py backend/apps/users/services/deferred_merge_guard.py backend/apps/users/services/deferred_merge_credential_synchronizer.py backend/apps/users/services/merge_convergence_service.py backend/apps/users/security/deferred_merge_execution_policy.py backend/apps/users/security/credential_entrypoints.py backend/apps/users/security/credential_entrypoint_manifest.json backend/apps/users/security/identity_merge_locking.py backend/apps/common/contracts/iam_tenant_inventory.py backend/apps/users/infrastructure/iam_tenant_inventory.py backend/apps/users/security/reconciliation_approval.py backend/apps/users/management/commands/report_identity_email_duplicates.py backend/apps/users/management/commands/resolve_identity_collision.py backend/apps/users/tasks/__init__.py backend/apps/users/tasks/identity_merge_maintenance.py backend/apps/users/tasks/identity_merge_requeue.py backend/apps/users/management/commands/expire_pending_identity_merges.py backend/apps/users/management/commands/requeue_identity_merge.py backend/apps/users/services/auth_service.py backend/apps/users/services/mfa_service.py backend/apps/users/services/user_admin_service.py backend/apps/users/api.py backend/apps/users/schemas/auth.py backend/apps/common/api/auth.py backend/config/settings/base.py backend/config/settings/prod.py .env.example backend/apps/users/tests/test_email_reconciliation.py backend/apps/users/tests/test_iam_tenant_inventory.py backend/apps/users/tests/test_deferred_merge_convergence.py backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py backend/apps/users/tests/test_identity_merge_locking.py backend/apps/users/tests/test_identity_merge_requeue.py backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py
git commit -m "feat(iam): gate and converge approved identity merges"
```

**Release N operational gate:** deploy Tasks 1–2, run the report to a protected
external path with both fail flags, and keep
`IAM_DEFERRED_MERGE_EXECUTION_ENABLED=false`. Signed approvals may create only
`APPROVED_PENDING_CONVERGENCE` rows while the coverage manifest is reviewed.
Enable effective execution only after the runtime signature, current build,
manifest digest, expiry and behavioral coverage gate all pass; then complete
each collision through owner-confirmed convergence, dormant fail-closed
convergence or, after separately cancelling any live pending operation, a
newly signed `--correct-email`. Stop before production Task 3 backfill. Do
not start Task 4 while any duplicate/invalid candidate, pending convergence or
invalid deferred relational proof remains.

---

### Task 3: Per-Row Identity UUID and Membership Backfill

**Files:**

- Modify: `backend/apps/users/models.py`
- Modify: `backend/apps/users/services/identity_foundation.py`
- Modify: `backend/apps/users/repositories/__init__.py`
- Modify: `backend/apps/users/repositories/membership_repository.py`
- Modify: `backend/apps/users/repositories/iam_tenant_registry_repository.py`
- Modify: `backend/apps/users/infrastructure/iam_tenant_inventory.py`
- Reuse: `backend/apps/users/security/identity_merge_locking.py`
- Reuse: `backend/apps/users/tasks/__init__.py`
- Create: `backend/apps/users/tasks/identity_backfill.py`
- Create: `backend/apps/users/management/commands/backfill_identity_foundation.py`
- Modify: `backend/apps/users/services/user_admin_service.py`
- Test: `backend/apps/users/tests/test_identity_backfill.py`
- Test: `backend/apps/users/tests/test_user_admin_service.py`

**Interfaces:**

- Produces `run_identity_backfill_batch(batch_size: int = 5000, *,
  apps_registry: Apps | None = None, using: str = "default") ->
  BackfillBatchResult`; production uses defaults, migration regressions inject
  historical models and the isolated PostgreSQL alias. The result separates
  `processed_candidates`, actual global-field updates, membership/state
  creates, invalid candidates and advisory-lock contention.
- Produces `backfill_identity_foundation --batch-size 5000 --dry-run`.
- New `User` creation dual-writes a fresh per-row UUID and normalized email while legacy fields remain readable.
- Produces idempotent `MembershipRepository.ensure_foundation(...)`, which
  creates the membership with `legacy_actor_user_id=<source User.pk>` and
  exactly one `TenantAuthorizationState` in the same tenant-scoped
  transaction.
- Produces idempotent
  `MembershipRepository.ensure_authorization_state(tenant_id)`, used by
  backfill even when every Identity row was already populated by an earlier
  attempt.
- Produces `ensure_legacy_actor_foundation(...)`: backfill first resolves an
  existing membership by immutable `(tenant_id, legacy_actor_user_id)`.
  Therefore an approved Task 2 merge is an idempotent prior run and the locked
  source actor is never resurrected as a second membership/Identity authority.
- Consumes Task 2 `ensure_identity_foundation()`; backfill must not duplicate
  or drift from the reconciliation initialization rules.
- Candidate discovery includes both missing global fields and tenant-scoped
  missing actor memberships, so an approval-only Task 2 initialization cannot
  make a User invisible to membership backfill.
- Seeds append-only `IamTenantRegistryEntry` rows from
  `LegacyIamTenantDiscovery` before tenant foundation writes and registers
  every later candidate/membership tenant in the same transaction. Completion
  requires `compatibility_tenants_missing_from_registry == 0`; all post-
  contract gates use `DurableIamTenantInventoryProvider` only.

- [ ] **Step 1: Write failing backfill tests**

```python
import uuid

import pytest
from django.db import models
from django.db.migrations.executor import MigrationExecutor

from apps.common.tenancy.context import clear_tenant_context, set_tenant_context
from apps.users.models import IamTenantRegistryEntry, TenantAuthorizationState, TenantMembership, User
from apps.users.repositories.membership_repository import MembershipRepository
from apps.users.services.user_admin_service import create_user
from apps.users.tasks.identity_backfill import run_identity_backfill_batch


@pytest.fixture
def identity_expand_apps(iam_migration_connection):
    executor = MigrationExecutor(iam_migration_connection)
    target = [("users", "0003_identity_expand")]
    executor.migrate(target)
    apps = executor.loader.project_state(target).apps
    apps.connection_alias = iam_migration_connection.alias
    return apps


@pytest.mark.django_db(transaction=True)
def test_backfill_assigns_distinct_uuid_per_row_and_no_membership_email(identity_expand_apps):
    ExpandUser = identity_expand_apps.get_model("users", "User")
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    first = ExpandUser.objects.create(username="one@example.com", email="One@Example.com", tenant_id=tenant_a)
    second = ExpandUser.objects.create(username="two@example.com", email="Two@Example.com", tenant_id=tenant_b)

    result = run_identity_backfill_batch(
        batch_size=5000,
        apps_registry=identity_expand_apps,
        using=identity_expand_apps.connection_alias,
    )

    first.refresh_from_db()
    second.refresh_from_db()
    assert result.updated_identities == 2
    assert first.identity_id != second.identity_id
    assert first.normalized_email == "one@example.com"
    assert second.normalized_email == "two@example.com"
    assert first.identity_legal_hold is False
    assert second.identity_legal_hold is False
    set_tenant_context(tenant_a)
    try:
        membership = TenantMembership.objects.get(identity_id=first.identity_id)
    finally:
        clear_tenant_context()
    assert not hasattr(membership, "email")
    assert membership.legacy_actor_user_id == first.pk
    assert result.created_authorization_states == 2
    assert set(
        IamTenantRegistryEntry.objects.values_list("tenant_id", flat=True)
    ) == {tenant_a, tenant_b}
    for tenant_id in (tenant_a, tenant_b):
        set_tenant_context(tenant_id)
        try:
            assert TenantAuthorizationState.objects.filter(
                tenant_id=tenant_id,
                policy_version=1,
            ).count() == 1
        finally:
            clear_tenant_context()


@pytest.mark.django_db(transaction=True)
def test_backfill_is_idempotent(identity_expand_apps):
    ExpandUser = identity_expand_apps.get_model("users", "User")
    user = ExpandUser.objects.create(
        username="stable@example.com",
        email="stable@example.com",
        tenant_id=uuid.uuid4(),
    )
    run_identity_backfill_batch()
    user.refresh_from_db()
    original = user.identity_id

    second = run_identity_backfill_batch()

    user.refresh_from_db()
    assert user.identity_id == original
    assert second.updated_identities == 0
    assert second.created_authorization_states == 0


@pytest.mark.django_db(transaction=True)
def test_backfill_does_not_resurrect_approved_merge_source_membership(
    approved_merged_collision_state,
):
    state = approved_merged_collision_state

    run_identity_backfill_batch()

    set_tenant_context(state.tenant_id)
    try:
        actor_memberships = TenantMembership.objects.filter(
            tenant_id=state.tenant_id,
            legacy_actor_user_id=state.source.pk,
            is_deleted=False,
        )
        assert actor_memberships.count() == 1
        assert actor_memberships.get().identity_id == state.canonical.identity_id
        assert not TenantMembership.objects.filter(
            tenant_id=state.tenant_id,
            identity_id=state.source.identity_id,
            is_deleted=False,
        ).exists()
    finally:
        clear_tenant_context()


@pytest.mark.django_db(transaction=True)
def test_backfill_creates_membership_when_approval_preinitialized_global_fields(
    pending_merge_source_without_membership,
):
    state = pending_merge_source_without_membership
    assert state.source.identity_id is not None
    assert state.source.normalized_email is not None

    result = run_identity_backfill_batch()

    assert result.updated_identities == 0
    assert result.created_memberships == 1
    set_tenant_context(state.source.tenant_id)
    try:
        membership = TenantMembership.objects.get(
            legacy_actor_user_id=state.source.pk,
            is_deleted=False,
        )
        assert membership.identity_id == state.source.identity_id
    finally:
        clear_tenant_context()


@pytest.mark.django_db(transaction=True)
def test_membership_foundation_dual_write_is_idempotent(identity):
    tenant_id = uuid.uuid4()
    kwargs = {
        "tenant_id": tenant_id,
        "identity_id": identity.identity_id,
        "legacy_actor_user_id": identity.pk,
        "display_name": "Ivan",
        "position": "",
        "status": TenantMembership.Status.ACTIVE,
    }
    first = MembershipRepository.ensure_foundation(**kwargs)
    second = MembershipRepository.ensure_foundation(**kwargs)

    assert second.membership.id == first.membership.id
    set_tenant_context(tenant_id)
    try:
        assert TenantAuthorizationState.objects.filter(tenant_id=tenant_id).count() == 1
    finally:
        clear_tenant_context()


@pytest.mark.django_db(transaction=True)
def test_backfill_repairs_missing_tenant_authorization_state(identity):
    tenant_id = uuid.uuid4()
    IamTenantRegistryEntry.objects.create(
        tenant_id=tenant_id,
        source_code="backfill_test",
    )
    set_tenant_context(tenant_id)
    try:
        TenantMembership.objects.create(
            tenant_id=tenant_id,
            identity_id=identity.identity_id,
            legacy_actor_user_id=identity.pk,
            display_name="Ivan",
            status=TenantMembership.Status.ACTIVE,
        )
    finally:
        clear_tenant_context()
    User.objects.filter(pk=identity.pk).update(tenant_id=tenant_id)

    result = run_identity_backfill_batch()

    assert result.updated_identities == 0
    assert result.created_authorization_states == 1
    set_tenant_context(tenant_id)
    try:
        assert TenantAuthorizationState.objects.filter(tenant_id=tenant_id).count() == 1
    finally:
        clear_tenant_context()


@pytest.mark.django_db(transaction=True)
def test_compatibility_user_creation_dual_writes_tenant_foundation(admin_actor, viewer_role):
    tenant_id = uuid.uuid4()
    user, _ = create_user(
        tenant_id=tenant_id,
        email="new@example.com",
        full_name="New User",
        roles=[viewer_role.name],
        actor_id=admin_actor,
    )

    assert user.security_status == User.SecurityStatus.MFA_SETUP_REQUIRED
    set_tenant_context(tenant_id)
    try:
        membership = TenantMembership.objects.get(identity_id=user.identity_id)
        assert membership.legacy_actor_user_id == user.pk
        assert TenantAuthorizationState.objects.filter(tenant_id=tenant_id).count() == 1
    finally:
        clear_tenant_context()
```

- [ ] **Step 2: Run and verify failure**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_identity_backfill.py -v
```

Expected: FAIL because the batched backfill does not exist.

- [ ] **Step 3: Implement an idempotent advisory-coordinated batch**

Use one UUID generation call per locked row and the same bootstrap/Identity
advisory namespace as merge execution:

```python
def run_identity_backfill_batch(
    batch_size: int = 5000,
    *,
    apps_registry=None,
    using: str = "default",
) -> BackfillBatchResult:
    user_model = User if apps_registry is None else apps_registry.get_model("users", "User")
    candidate_pks = collect_backfill_candidate_pks(
        user_model=user_model,
        batch_size=batch_size * 2,
        using=using,
        apps_registry=apps_registry,
    )
    with transaction.atomic(using=using):
        locked_pks, contended = IdentityMergeLockCoordinator.try_lock_backfill_participants(
            user_model=user_model,
            candidate_pks=candidate_pks,
            limit=batch_size,
            using=using,
        )
        rows = list(
            user_model.objects.using(using)
            .select_for_update()
            .filter(pk__in=locked_pks)
            .order_by("pk")
        )
        return _run_locked_identity_backfill_batch(
            user_model=user_model,
            rows=rows,
            contended=contended,
            using=using,
            apps_registry=apps_registry,
        )


def _run_locked_identity_backfill_batch(
    *,
    user_model,
    rows,
    contended: int,
    using: str,
    apps_registry,
) -> BackfillBatchResult:
    updated_identities = 0
    memberships = 0
    authorization_states = 0
    invalid_candidates = 0
    membership_intents = []
    for user in rows:
        try:
            foundation = ensure_identity_foundation(user)
        except ValidationError:
            invalid_candidates += 1
            continue
        updated_identities += int(foundation.changed)
        if user.tenant_id:
            membership_intents.append((user, foundation))

    inventory_tenant_ids = set(
        LegacyIamTenantDiscovery(using=using).iter_tenant_ids()
    ) | set(DurableIamTenantInventoryProvider(using=using).iter_tenant_ids())
    tenant_ids = sorted(
        inventory_tenant_ids | {user.tenant_id for user, _ in membership_intents},
        key=lambda value: value.bytes,
    )
    # Complete the global registry rank for every tenant before the first RLS
    # table query. ensure_registered inserts-or-observes and returns the row
    # locked in this transaction.
    for tenant_id in tenant_ids:
        IamTenantRegistryRepository.ensure_registered(
            tenant_id,
            source_code="identity_backfill",
            using=using,
        )

    for tenant_id in tenant_ids:
        # Closed tenant table rank starts with TenantAuthorizationState, then
        # TenantMembership. This task does not touch RoleBinding or grants.
        state_result = MembershipRepository.ensure_authorization_state(
            tenant_id,
            using=using,
            apps_registry=apps_registry,
        )
        authorization_states += int(state_result.created)
        tenant_intents = sorted(
            (
                (user, foundation)
                for user, foundation in membership_intents
                if user.tenant_id == tenant_id
            ),
            key=lambda item: item[0].pk,
        )
        for user, foundation in tenant_intents:
            ensured = MembershipRepository.ensure_legacy_actor_foundation(
                tenant_id,
                legacy_actor_user_id=user.pk,
                default_identity_id=foundation.identity_id,
                display_name=user.get_full_name() or user.username,
                position="",
                status=(
                    TenantMembership.Status.ACTIVE
                    if user.is_active
                    else TenantMembership.Status.DISABLED
                ),
                using=using,
                apps_registry=apps_registry,
            )
            memberships += int(ensured.membership_created)

    return BackfillBatchResult(
        processed_candidates=len(rows),
        updated_identities=updated_identities,
        created_memberships=memberships,
        created_authorization_states=authorization_states,
        invalid_candidates=invalid_candidates,
        contended_candidates=contended,
    )
```

Add `test_two_tenant_backfill_locks_complete_registry_rank_before_rls` with a
database execute wrapper over one A/B batch. It asserts UUID-sorted registry
insert/lock statements for both tenants occur before the first
`TenantAuthorizationState`, `TenantMembership`, RoleBinding or grant query;
the trace must never be `registry(A) -> tenant(A) -> registry(B)`.

`collect_backfill_candidate_pks()` is a read-only union of two incomplete
shapes: Users missing any global foundation field, and—inside each explicit
`IamTenantInventoryProvider` RLS context—legacy Users for that tenant whose
`legacy_actor_user_id` membership does not exist. Thus a Task 2 approval that
already filled every global column but intentionally created no membership is
still selected. The locked phase rechecks both predicates before mutation.

For bootstrap rows with `identity_id=NULL`, the common coordinator uses a
reserved advisory-key namespace derived from integer User PK. Every merge,
foundation and user-admin writer takes sorted bootstrap-PK keys first, then
sorted non-null Identity-UUID keys, then materializes `User` row locks in PK
order. Backfill uses `pg_try_advisory_xact_lock` only to skip a busy candidate;
target `select_for_update()` never uses `skip_locked`. A contended count forces
rescheduling and cannot be mistaken for completion. Tenant writes are not
performed inside the User-PK loop. The batch first materializes every
membership intent, then enters tenants once in ascending UUID-byte order.
It ensures sorted append-only registry rows before entering RLS. Within each
tenant it locks/creates `TenantAuthorizationState` first, then
`TenantMembership` rows in actor-PK order; later writers extend the closed
rank with `RoleBinding` and `TenantSessionGrant`. Candidate/hold predicates
and the action digest are rechecked only after this total order is
established.

Add a two-connection barrier regression named
`test_backfill_opposite_pk_tenant_shapes_do_not_deadlock`. Its fixtures assign
User PK order to tenant order as `B,A` in one batch and `A,B` in the other,
set a bounded PostgreSQL `lock_timeout`, and run both batches concurrently.
The only accepted outcomes are serialized success or the typed advisory
contention result; SQLSTATE `40P01` and a partially created tenant foundation
fail the test.

`ensure_identity_foundation()` locks/CASes the current row and returns whether
it changed any field, including backfilling `identity_legal_hold=False`.
`updated_identities` counts only rows changed by that
helper, so a Task 2 partial initialization remains an idempotent prior run.
The Celery task reschedules until both `processed_candidates` and
`contended_candidates` are zero. If a batch
returns `invalid_candidates > 0`, it emits only row PKs plus the protected
report digest, stops rescheduling and fails the release gate; it never logs raw
email and never rolls back valid rows solely because another row is invalid.
After `--correct-email` resolves those rows, the operator reruns the task.

- [ ] **Step 4: Add tenant-foundation and new-row dual write**

Create `MembershipRepository.ensure_authorization_state(tenant_id)` as a
tenant-scoped idempotent `get_or_create` of
`TenantAuthorizationState(tenant_id=tenant_id, policy_version=1)`.
`ensure_foundation()` sets tenant context, `get_or_create`s the membership by
`(tenant_id, identity_id, is_deleted=False)`, invokes the same private
current-context state helper, and clears context. The public methods do not
nest or prematurely clear tenant context. A retry returns the original rows.
The create path requires `legacy_actor_user_id`; an existing row must have the
same actor or the repository fails closed. Neither backfill nor a retry may
rewrite that immutable mapping.

`ensure_legacy_actor_foundation()` instead looks up the actor mapping first.
When no row exists it delegates to `ensure_foundation()` with
`default_identity_id`. When a row exists with a different canonical
`identity_id`, it accepts the row only if one locked
`IdentityMergeOperation` in `APPROVED_DEFERRED`, `READY_TO_FINALIZE` or
`FINALIZED` binds that exact source actor row/source Identity/canonical
Identity and its synthetic normalized email, and the membership graph is the
exact convergence result. Audit/outbox rows are supporting evidence, never
runtime mapping authority. It returns the existing membership without
changing either ID. Missing, duplicate or mismatched relational proof is an
invalid-candidate gate failure, never an automatic repair. This is the only
backfill exception to the usual actor-equals-Identity row shape.

The backfill invokes `ensure_authorization_state()` for every distinct non-null
legacy `User.tenant_id` on every run, including a run with zero Identity rows
to update. This repairs a partial prior attempt instead of treating
`identity_id IS NOT NULL` as proof that tenant policy state exists.

This repository currently has no authoritative `Tenant` model or tenant
provisioning service to hook into. Therefore every membership creation is the
provisioning boundary. Modify the existing
`users.services.user_admin_service.create_user()` compatibility path to call
`ensure_foundation()` inside its transaction. Plan 2 and any future dedicated
tenant-provisioning service must call this same repository method rather than
creating a membership directly.

Create `IdentityUserManager(UserManager)` and use it on `User`. `_create_user()`
supplies `identity_id=uuid.uuid4()`, normalized email/version and
`security_version=1` only when callers did not explicitly provide them. Its
initial status is `PENDING_CREDENTIALS` without a usable password,
`MFA_SETUP_REQUIRED` with a usable password but no confirmed legacy factor,
`ACTIVE` only with `mfa_enabled=True` plus a non-empty legacy secret, and
`LOCKED` when `is_active=False`. It does not make `identity_id` a model-field
default.

- [ ] **Step 5: Add dry-run command**

`--dry-run` reports counts without mutation. Non-dry execution repeatedly calls the batch function with the requested batch size. `--tenant-id` limits membership creation but still fills global Identity fields.

- [ ] **Step 6: Run backfill tests and worker registration checks**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_identity_backfill.py apps/users/tests/test_email_reconciliation.py apps/users/tests/test_user_admin_service.py -v
docker compose -f docker-compose.dev.yml exec backend python manage.py backfill_identity_foundation --dry-run --batch-size 5000
```

Expected: PASS.

- [ ] **Step 7: Commit Release N+1 backfill**

```bash
git add backend/apps/users/models.py backend/apps/users/services/identity_foundation.py backend/apps/users/repositories/__init__.py backend/apps/users/repositories/membership_repository.py backend/apps/users/repositories/iam_tenant_registry_repository.py backend/apps/users/infrastructure/iam_tenant_inventory.py backend/apps/users/tasks backend/apps/users/management/commands/backfill_identity_foundation.py backend/apps/users/services/user_admin_service.py backend/apps/users/tests/test_identity_backfill.py backend/apps/users/tests/test_user_admin_service.py backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py
git commit -m "feat(iam): backfill identity tenant foundation"
```

**Release N+1 manual gate:** rerun the reconciliation report. Duplicate groups
and invalid candidates must both be zero. Apply only approved typed
`resolve_identity_collision` operations; never use production SQL or
unapproved automatic merge. Record approval outside git before Task 4.

---

### Task 4: Contract Constraints After Clean Reconciliation

**Files:**

- Modify: `backend/apps/users/models.py`
- Modify: `backend/apps/users/models_identity.py`
- Modify: `backend/apps/users/models_membership.py`
- Create: `backend/apps/users/migrations/0004_identity_contract_constraints.py`
- Create: `backend/apps/users/management/commands/preflight_identity_contract.py`
- Reuse: `backend/apps/common/contracts/iam_tenant_inventory.py`
- Reuse: `backend/apps/users/infrastructure/iam_tenant_inventory.py`
- Modify: `infra/scripts/migration_linter.py`
- Create: `infra/scripts/tests/test_migration_linter.py`
- Test: `backend/apps/users/tests/test_identity_contract_constraints.py`
- Test: `backend/apps/users/tests/test_identity_migration_reversibility.py`

**Interfaces:**

- Produces final `UNIQUE NOT NULL users.User.identity_id`.
- Produces final `UNIQUE NOT NULL users.User.normalized_email`.
- Produces final non-null `email_normalization_version`, `security_version`,
  `security_status` and `identity_legal_hold`, plus a DB check restricting
  status to the four `SecurityStatus` values. Contract state changes
  `identity_legal_hold` to `BooleanField(default=False, editable=False)` only
  after the N+1 false backfill is proven complete.
- Builds `users_security_status_idx` and `users_identity_legal_hold_idx`
  concurrently; Release N never creates these indexes implicitly.
- Produces DB FKs from `TenantMembership`, `IdentitySession`,
  `PreAuthChallenge`, `MfaCredential`, `BackupCode` and `RecoveryToken` to
  `users.User.identity_id`. `RecoveryOperation` derives Identity and purpose
  only through its reverse OneToOne token relation and has no duplicate
  subject/purpose authority.
- Only after that same `users.User.identity_id` unique constraint exists,
  converts `IdentityMergeOperation.source_identity_id` and
  `canonical_identity_id` from the raw UUID columns created by `0003` into
  validated DB FKs to `users.User.identity_id`. `0003` must contain neither
  FK; Django state changes to distinct `ForeignKey(to_field="identity_id",
  db_column=...)` relations only in `0004`.
- Contracts `TenantMembership.legacy_actor_user` to `NOT NULL`; its existing
  integer-PK `ON DELETE PROTECT` FK and write-once trigger remain in force.
- Preserves partial active-membership uniqueness, tenant authorization state uniqueness and grant FKs.
- Preserves the integer-PK `legacy_actor_user` FK and rejects null/missing
  membership actor mappings before contract; Task 2 separately rejects
  ambiguous overlapping merge footprints.
- Produces `DurableIamTenantInventoryProvider.iter_tenant_ids() ->
  Iterator[UUID]`; every post-backfill global IAM gate uses registry authority
  before entering one explicit RLS context at a time. Contract preflight also
  proves `LegacyIamTenantDiscovery - registry == empty`.
- Produces `run_identity_contract_preflight(*, inventory,
  apps_registry: Apps | None = None, using: str = "default")`; when historical
  `Apps` is supplied, every model is resolved from it and every query uses the
  supplied alias. Migration tests may not fall back to runtime model imports or
  the default database.

- [ ] **Step 1: Write failing constraint tests**

```python
import json
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.core.management import CommandError, call_command
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from apps.common.models.rbac import RoleBinding
from apps.common.tenancy.context import clear_tenant_context, set_tenant_context
from apps.users.infrastructure.iam_tenant_inventory import (
    DurableIamTenantInventoryProvider,
    LegacyIamTenantDiscovery,
)
from apps.users.management.commands.preflight_identity_contract import (
    run_identity_contract_preflight,
)
from apps.users.repositories.iam_tenant_registry_repository import (
    IamTenantRegistryRepository,
)
from apps.users.models import (
    IdentityMergeOperation,
    IdentitySession,
    MfaCredential,
    TenantAuthorizationState,
    TenantGrantSwitchOperation,
    TenantMembership,
    TenantSessionGrant,
    User,
)


@pytest.mark.django_db(transaction=True)
def test_normalized_email_is_globally_unique_after_contract():
    User.objects.create_user(username="one", email="ivan@example.com", password="Pass1!")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            User.objects.create_user(username="two", email="IVAN@example.com", password="Pass1!")


@pytest.mark.django_db(transaction=True)
def test_identity_reference_foreign_keys_reject_orphans():
    missing = uuid.uuid4()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            IdentitySession.objects.create(
                identity_id=missing,
                security_version_snapshot=1,
                expires_at="2030-01-01T00:00:00Z",
            )


@pytest.mark.django_db(transaction=True)
def test_merge_identity_reference_foreign_keys_reject_orphans(identity):
    missing = uuid.uuid4()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            IdentityMergeOperation.objects.create(
                source_user=identity,
                source_identity_id=identity.identity_id,
                canonical_identity_id=missing,
                state="approved_pending_convergence",
                approval_reference="IAM-CHANGE-ORPHAN",
                approval_digest="0" * 64,
                approval_key_id="test-key",
                approval_signature=b"signature",
                approved_at=timezone.now(),
                approval_expires_at=timezone.now() + timedelta(hours=1),
                synthetic_normalized_email=(
                    f"merged+{identity.identity_id}@identity.invalid"
                ),
                retention_until=timezone.now() + timedelta(days=365),
            )


@pytest.mark.django_db(transaction=True)
def test_identity_security_status_rejects_unknown_database_value(identity):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            User.objects.filter(pk=identity.pk).update(
                security_status="free_form_status",
            )


def test_identity_legal_hold_is_contracted_after_false_backfill():
    field = User._meta.get_field("identity_legal_hold")
    assert field.null is False
    assert field.default is False


def test_membership_actor_is_contracted_non_null_and_protected():
    field = TenantMembership._meta.get_field("legacy_actor_user")
    assert field.null is False
    assert field.remote_field.on_delete is models.PROTECT


@pytest.mark.django_db
def test_user_contract_lookup_indexes_have_exact_columns():
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(
            cursor,
            User._meta.db_table,
        )
    assert constraints["users_security_status_idx"]["columns"] == [
        "security_status",
    ]
    assert constraints["users_security_status_idx"]["index"] is True
    assert constraints["users_identity_legal_hold_idx"]["columns"] == [
        "identity_legal_hold",
    ]
    assert constraints["users_identity_legal_hold_idx"]["index"] is True


@pytest.mark.django_db(transaction=True)
def test_active_membership_unique_but_soft_deleted_history_is_allowed(identity):
    tenant_id = uuid.uuid4()
    set_tenant_context(tenant_id)
    try:
        with transaction.atomic():
            IamTenantRegistryRepository.ensure_registered(
                tenant_id,
                source_code="contract_test",
            )
            TenantMembership.objects.create(
                tenant_id=tenant_id,
                identity_id=identity.identity_id,
                legacy_actor_user_id=identity.pk,
                display_name="Ivan",
                status=TenantMembership.Status.ACTIVE,
                is_deleted=True,
            )
            current = TenantMembership.objects.create(
                tenant_id=tenant_id,
                identity_id=identity.identity_id,
                legacy_actor_user_id=identity.pk,
                display_name="Ivan",
                status=TenantMembership.Status.ACTIVE,
            )
            assert current.is_deleted is False
    finally:
        clear_tenant_context()


@pytest.mark.django_db(transaction=True)
def test_grant_generic_soft_delete_is_rejected_by_database(identity, active_membership):
    session = IdentitySession.objects.create(
        identity_id=identity.identity_id,
        security_version_snapshot=identity.security_version,
        expires_at=timezone.now() + timedelta(days=7),
    )
    set_tenant_context(active_membership.tenant_id)
    try:
        grant = TenantSessionGrant.objects.create(
            tenant_id=active_membership.tenant_id,
            session=session,
            membership=active_membership,
            context_version=1,
            issued_at=timezone.now(),
            expires_at=timezone.now() + timedelta(hours=8),
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                TenantSessionGrant.objects.filter(pk=grant.id).update(is_deleted=True)
    finally:
        clear_tenant_context()


@pytest.mark.parametrize(
    ("column", "replacement"),
    [
        ("membership_id", uuid.uuid4()),
        ("tenant_id", uuid.uuid4()),
        ("context_version", 999),
    ],
)
@pytest.mark.django_db(transaction=True)
def test_grant_append_only_trigger_rejects_authority_rewrite(
    active_grant,
    column,
    replacement,
):
    set_tenant_context(active_grant.tenant_id)
    try:
        with pytest.raises(IntegrityError, match="grant_append_only"):
            with transaction.atomic():
                TenantSessionGrant.objects.filter(pk=active_grant.id).update(
                    **{column: replacement},
                )
    finally:
        clear_tenant_context()


@pytest.mark.django_db(transaction=True)
def test_tenant_authorization_state_soft_delete_is_rejected_by_database():
    tenant_id = uuid.uuid4()
    set_tenant_context(tenant_id)
    try:
        with transaction.atomic():
            IamTenantRegistryRepository.ensure_registered(
                tenant_id,
                source_code="contract_test",
            )
            state = TenantAuthorizationState.objects.create(
                tenant_id=tenant_id,
                policy_version=1,
            )
            with pytest.raises(IntegrityError):
                with transaction.atomic():
                    TenantAuthorizationState.objects.filter(pk=state.id).update(
                        is_deleted=True,
                    )
    finally:
        clear_tenant_context()


@pytest.mark.django_db(transaction=True)
def test_only_one_unrevoked_totp_credential_per_identity(identity):
    MfaCredential.objects.create(
        identity_id=identity.identity_id,
        credential_type="totp",
        encrypted_secret=b"ciphertext-one",
        nonce=b"nonce-one-12",
        key_id="test-key",
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            MfaCredential.objects.create(
                identity_id=identity.identity_id,
                credential_type="totp",
                encrypted_secret=b"ciphertext-two",
                nonce=b"nonce-two-12",
                key_id="test-key",
            )


@pytest.mark.django_db(transaction=True)
def test_contract_preflight_rejects_missing_tenant_authorization_state(identity):
    tenant_id = uuid.uuid4()
    User.objects.filter(pk=identity.pk).update(tenant_id=tenant_id)
    set_tenant_context(tenant_id)
    try:
        with transaction.atomic():
            IamTenantRegistryRepository.ensure_registered(
                tenant_id,
                source_code="contract_test",
            )
            TenantMembership.objects.create(
                tenant_id=tenant_id,
                identity_id=identity.identity_id,
                legacy_actor_user_id=identity.pk,
                display_name="Ivan",
                status=TenantMembership.Status.ACTIVE,
            )
    finally:
        clear_tenant_context()

    with pytest.raises(CommandError, match="tenants_without_authorization_state"):
        call_command("preflight_identity_contract")


@pytest.mark.django_db(transaction=True)
def test_legacy_discovery_seeds_complete_durable_tenant_inventory(
    identity,
    identity_session,
    role,
):
    legacy_tenant = uuid.uuid4()
    binding_tenant = uuid.uuid4()
    expected_tenant = uuid.uuid4()
    target_tenant = uuid.uuid4()
    result_tenant = uuid.uuid4()
    registry_only_tenant = uuid.uuid4()
    User.objects.filter(pk=identity.pk).update(tenant_id=legacy_tenant)
    RoleBinding.objects.create(
        tenant_id=binding_tenant,
        user_id=uuid.uuid4(),
        role=role,
    )
    with transaction.atomic():
        for tenant_id in sorted(
            {expected_tenant, target_tenant, result_tenant},
            key=lambda value: value.bytes,
        ):
            IamTenantRegistryRepository.ensure_registered(
                tenant_id,
                source_code="switch_test",
            )
        TenantGrantSwitchOperation.objects.create(
            operation_id=uuid.uuid4(),
            session=identity_session,
            request_fingerprint="0" * 64,
            expected_current_tenant_id=expected_tenant,
            target_tenant_id=target_tenant,
            result_tenant_id=result_tenant,
        )

    discovered = set(LegacyIamTenantDiscovery().iter_tenant_ids())
    assert discovered == {
        legacy_tenant,
        binding_tenant,
        expected_tenant,
        target_tenant,
        result_tenant,
    }
    with transaction.atomic():
        for tenant_id in sorted(
            discovered | {registry_only_tenant},
            key=lambda value: value.bytes,
        ):
            IamTenantRegistryRepository.ensure_registered(
                tenant_id,
                source_code="contract_test",
            )
        set_tenant_context(registry_only_tenant)
        try:
            TenantMembership.objects.create(
                tenant_id=registry_only_tenant,
                identity_id=identity.identity_id,
                legacy_actor_user_id=identity.pk,
                display_name="Registry only",
                status=TenantMembership.Status.DRAFT,
            )
        finally:
            clear_tenant_context()

    assert set(DurableIamTenantInventoryProvider().iter_tenant_ids()) == (
        discovered | {registry_only_tenant}
    )


@pytest.mark.django_db(transaction=True)
def test_preflight_queries_each_rls_table_only_inside_its_tenant_context(
    valid_two_tenant_expand_state,
    tenant_context_spy,
):
    run_identity_contract_preflight(
        inventory=valid_two_tenant_expand_state.inventory,
        tenant_scope=tenant_context_spy,
    )

    assert tenant_context_spy.entered == [
        valid_two_tenant_expand_state.tenant_a,
        valid_two_tenant_expand_state.tenant_b,
    ]
    assert tenant_context_spy.query_trace == [
        (valid_two_tenant_expand_state.tenant_a, "TenantMembership"),
        (valid_two_tenant_expand_state.tenant_a, "TenantAuthorizationState"),
        (valid_two_tenant_expand_state.tenant_a, "TenantSessionGrant"),
        (valid_two_tenant_expand_state.tenant_b, "TenantMembership"),
        (valid_two_tenant_expand_state.tenant_b, "TenantAuthorizationState"),
        (valid_two_tenant_expand_state.tenant_b, "TenantSessionGrant"),
    ]
    assert tenant_context_spy.current_tenant is None


@pytest.mark.parametrize(
    ("violation", "expected_key"),
    [
        ("membership_identity_orphan", "membership_identity_orphans"),
        ("missing_membership", "users_without_membership"),
        ("membership_actor_null", "membership_actor_nulls"),
        ("unapproved_actor_mapping", "unapproved_actor_identity_mappings"),
        ("missing_authorization_state", "tenants_without_authorization_state"),
        ("grant_membership_tenant_mismatch", "grant_membership_tenant_mismatches"),
        ("grant_session_identity_mismatch", "grant_session_identity_mismatches"),
    ],
)
@pytest.mark.django_db(transaction=True)
def test_expand_preflight_rejects_every_tenant_integrity_violation(
    expand_contract_violation_factory,
    violation,
    expected_key,
):
    # The factory uses MigrationExecutor state 0003, before Task 4 FKs/checks,
    # so every invalid legacy shape can be represented honestly.
    scenario = expand_contract_violation_factory(violation)

    with pytest.raises(CommandError) as caught:
        run_identity_contract_preflight(
            inventory=scenario.inventory,
            tenant_scope=scenario.tenant_scope,
        )

    counts = json.loads(str(caught.value))
    assert counts[expected_key] == 1
    assert sum(counts.values()) == 1
    assert scenario.tenant_scope.query_trace
    assert all(
        active_tenant == row_tenant
        for active_tenant, row_tenant in scenario.tenant_scope.row_trace
    )
```

Add two non-parameterized preflight cases because their counters legitimately
overlap other gates: a pending convergence must report
`pending_merge_convergences=1` (and may also report the unresolved duplicate),
and a forged/incomplete deferred operation must report
`invalid_deferred_merge_proofs=1` plus any resulting actor-mapping violation.
Neither test asserts that the sum of all counters is one. A matching
`APPROVED_DEFERRED` relational graph passes both counters even while its raw
source email/username remain for legacy compatibility.
Add a third non-parameterized case with a legacy `RoleBinding.tenant_id` absent
from `IamTenantRegistryEntry`; it must report
`compatibility_tenants_missing_from_registry=1`. The paired registry-only
DRAFT membership case must be visited by the durable provider even when no
legacy User, RoleBinding or switch operation references that tenant.

Extend `iam_factories.py` with `valid_two_tenant_expand_state`,
`tenant_context_spy` and `expand_contract_violation_factory`. The violation
factory migrates only its isolated transactional database state to
`0003_identity_expand`, inserts one of the seven representable pre-contract invalid shapes,
and restores `0004` in teardown; it never disables FK/RLS checks or relies on
superuser bypass. `run_identity_contract_preflight()` is the command's
injectable function, so tests exercise the same production queries and JSON
error payload rather than a duplicate checker.

The other two diagnostic counters remain in preflight, but
`tenants_with_multiple_authorization_states` and
`sessions_with_multiple_open_grants` cannot be seeded honestly even at 0003:
their unique constraints are part of expand. Task 1 therefore tests those two
database constraints directly. Do not disable constraints merely to fabricate
a preflight integration case.

- [ ] **Step 2: Add a deployment preflight**

Reuse Task 2's `IamTenantInventoryProvider` with
`DurableIamTenantInventoryProvider`. `LegacyIamTenantDiscovery` separately
reads the primary/global compatibility union of:

- `User.tenant_id`;
- `RoleBinding.tenant_id`;
- `TenantGrantSwitchOperation.expected_current_tenant_id`;
- `TenantGrantSwitchOperation.target_tenant_id`;
- `TenantGrantSwitchOperation.result_tenant_id`.

Discovery does not query `TenantMembership`, `TenantAuthorizationState` or
`TenantSessionGrant` without RLS. Preflight first requires
`compatibility_tenants_missing_from_registry == 0`, then discards discovery
output and injects the durable provider into Tasks 4, 14 and Task 17. Those
commands must not implement tenant discovery or depend on `User.tenant_id`.
Registry-only tenants remain inventoried after legacy columns and switch rows
are absent.

`preflight_identity_contract` first performs global Identity/session checks,
then iterates `inventory.iter_tenant_ids()`. For each returned tenant it sets
that exact ContextVar and database RLS context, runs queries that also filter
`tenant_id=tenant_id`, and restores the prior context in `finally`. It exits
non-zero unless all conditions are true:

```python
checks = {
    "identity_id_nulls": User.objects.filter(identity_id__isnull=True).count(),
    "normalized_email_nulls": User.objects.filter(normalized_email__isnull=True).count(),
    "normalization_version_nulls": User.objects.filter(email_normalization_version__isnull=True).count(),
    "security_version_nulls": User.objects.filter(security_version__isnull=True).count(),
    "security_status_nulls": User.objects.filter(security_status__isnull=True).count(),
    "identity_legal_hold_nulls": User.objects.filter(identity_legal_hold__isnull=True).count(),
    "invalid_security_statuses": User.objects.exclude(
        security_status__in=User.SecurityStatus.values,
    ).count(),
    "duplicate_identity_ids": duplicate_count("identity_id"),
    "duplicate_normalized_emails": duplicate_count("normalized_email"),
    "invalid_email_candidates": invalid_identity_candidate_count(),
    "session_identity_orphans": session_identity_orphan_count(),
    "merge_source_identity_orphans": merge_source_identity_orphan_count(),
    "merge_canonical_identity_orphans": merge_canonical_identity_orphan_count(),
    "pending_merge_convergences": pending_merge_convergence_count(),
    "invalid_deferred_merge_proofs": invalid_deferred_merge_proof_count(inventory),
    "compatibility_tenants_missing_from_registry": compatibility_registry_gap_count(),
    "membership_actor_nulls": membership_actor_null_count(inventory),
    "unapproved_actor_identity_mappings": unapproved_actor_mapping_count(inventory),
    **tenant_scoped_integrity_counts(inventory),
}
if any(checks.values()):
    raise CommandError(json.dumps(checks, sort_keys=True))
```

`tenant_scoped_integrity_counts()` aggregates these exact per-tenant counts:

```python
{
    "membership_identity_orphans": ...,
    "users_without_membership": ...,
    "tenants_without_authorization_state": ...,
    "tenants_with_multiple_authorization_states": ...,
    "sessions_with_multiple_open_grants": ...,
    "grant_membership_tenant_mismatches": ...,
    "grant_session_identity_mismatches": ...,
}
```

An authorization-state count is valid only when it equals one. An open grant
means `superseded_at IS NULL AND revoked_at IS NULL`, regardless of expiry.
`users_without_membership` counts, inside each explicit tenant context, every
legacy User with that `tenant_id` for which no non-deleted membership has
`legacy_actor_user_id=User.pk`; global-field completeness never substitutes
for this actor foundation.
Grant integrity compares each grant to its exact membership and global session;
it does not infer a tenant from `session_id`. New tenant/membership creation is
covered by the Task 3 dual-write test. Tests seed two tenants and assert via a
spy context manager that each RLS table is queried only while its own tenant is
active; a deliberately orphaned membership and each grant mismatch must make
the command fail.

An actor mapping is ordinary when the actor User's `identity_id` equals the
membership canonical `identity_id`. A differing pair is valid only when one
locked `IdentityMergeOperation` in `APPROVED_DEFERRED`,
`READY_TO_FINALIZE` or `FINALIZED` binds the exact actor row, source Identity,
canonical Identity, synthetic normalized email and resulting membership.
Pending/cancelled/expired operations and audit JSON never authorize the
mapping. Preflight counts every other differing mapping as
`unapproved_actor_identity_mappings`; it separately counts any incomplete,
duplicated or contradictory realized-operation graph in
`invalid_deferred_merge_proofs`, without reading raw email or disabling RLS.

- [ ] **Step 3: Create concurrent unique indexes and attach constraints**

Set migration `atomic = False`. Use `SeparateDatabaseAndState` and exact PostgreSQL operations:

```sql
CREATE UNIQUE INDEX CONCURRENTLY users_identity_id_uniq_idx
    ON users_users (identity_id);
ALTER TABLE users_users
    ADD CONSTRAINT users_identity_id_uniq
    UNIQUE USING INDEX users_identity_id_uniq_idx;

CREATE UNIQUE INDEX CONCURRENTLY users_normalized_email_uniq_idx
    ON users_users (normalized_email);
ALTER TABLE users_users
    ADD CONSTRAINT users_normalized_email_uniq
    UNIQUE USING INDEX users_normalized_email_uniq_idx;

CREATE INDEX CONCURRENTLY users_security_status_idx
    ON users_users (security_status);
CREATE INDEX CONCURRENTLY users_identity_legal_hold_idx
    ON users_users (identity_legal_hold);
```

Represent the two non-unique indexes in Django state with `AddIndex` inside
the same `SeparateDatabaseAndState(state_operations=[...])`; keep the model
fields themselves at `db_index=False` so a future autodetector cannot also
create blocking duplicate indexes. Add the matching named `models.Index`
entries to `User.Meta.indexes`. The unique constraints attached above already
provide indexes for `identity_id` and `normalized_email`; do not create a
second non-unique index for either column.

For each non-null field, including the backfilled
`identity_legal_hold`, add `CHECK (...) NOT VALID`, `VALIDATE CONSTRAINT`, set
`NOT NULL`, then drop the temporary check. Only the state operation then sets
the model default to `False`; no database default is required. Retain a validated
`users_identity_security_status_valid` check whose allowed values are exactly
`pending_credentials`, `mfa_setup_required`, `active` and `locked`. Do not
create a unique key on `(normalized_email, email_normalization_version)`.

- [ ] **Step 4: Add and validate Identity foreign keys**

After `identity_id` is unique:

```sql
ALTER TABLE users_identity_sessions
    ADD CONSTRAINT users_identity_sessions_identity_fk
    FOREIGN KEY (identity_id)
    REFERENCES users_users(identity_id)
    ON DELETE RESTRICT
    NOT VALID;
ALTER TABLE users_identity_sessions
    VALIDATE CONSTRAINT users_identity_sessions_identity_fk;

ALTER TABLE users_identity_merge_operations
    ADD CONSTRAINT users_merge_source_identity_fk
    FOREIGN KEY (source_identity_id)
    REFERENCES users_users(identity_id)
    ON DELETE RESTRICT
    NOT VALID;
ALTER TABLE users_identity_merge_operations
    VALIDATE CONSTRAINT users_merge_source_identity_fk;

ALTER TABLE users_identity_merge_operations
    ADD CONSTRAINT users_merge_canonical_identity_fk
    FOREIGN KEY (canonical_identity_id)
    REFERENCES users_users(identity_id)
    ON DELETE RESTRICT
    NOT VALID;
ALTER TABLE users_identity_merge_operations
    VALIDATE CONSTRAINT users_merge_canonical_identity_fk;

ALTER TABLE users_tenant_memberships
    ADD CONSTRAINT users_memberships_identity_fk
    FOREIGN KEY (identity_id)
    REFERENCES users_users(identity_id)
    ON DELETE RESTRICT
    NOT VALID;
ALTER TABLE users_tenant_memberships
    VALIDATE CONSTRAINT users_memberships_identity_fk;

ALTER TABLE users_tenant_memberships
    ADD CONSTRAINT users_memberships_actor_nn
    CHECK (legacy_actor_user_id IS NOT NULL) NOT VALID;
ALTER TABLE users_tenant_memberships
    VALIDATE CONSTRAINT users_memberships_actor_nn;
ALTER TABLE users_tenant_memberships
    ALTER COLUMN legacy_actor_user_id SET NOT NULL;
ALTER TABLE users_tenant_memberships
    DROP CONSTRAINT users_memberships_actor_nn;
```

In each model replace the temporary `identity_id = UUIDField(...)` declaration
with a relation named `identity`:

```python
identity = models.ForeignKey(
    settings.AUTH_USER_MODEL,
    to_field="identity_id",
    db_column="identity_id",
    on_delete=models.PROTECT,
    related_name="identity_sessions",  # use a distinct related_name per model
)
```

The Django attname remains `identity_id`, so repository filters such as
`.filter(identity_id=identity_uuid)` do not change. Use distinct concrete
`related_name` values for memberships, sessions, challenges, MFA credentials,
backup codes and recovery tokens. Do not rename any physical column.
In Django state, update the membership, unrevoked-TOTP and open-recovery-token
`UniqueConstraint.fields` entries from the temporary field name
`identity_id` to relation name `identity`; their database columns and
constraint names remain unchanged.

For `IdentityMergeOperation`, use two distinct relation names while retaining
the physical UUID columns and integer compatibility FK:

```python
source_identity = models.ForeignKey(
    settings.AUTH_USER_MODEL,
    to_field="identity_id",
    db_column="source_identity_id",
    on_delete=models.PROTECT,
    related_name="merge_operations_as_source_identity",
)
canonical_identity = models.ForeignKey(
    settings.AUTH_USER_MODEL,
    to_field="identity_id",
    db_column="canonical_identity_id",
    on_delete=models.PROTECT,
    related_name="merge_operations_as_canonical_identity",
)
```

`source_user` remains the separate `PROTECT` FK to the existing integer PK.
Use `SeparateDatabaseAndState`: the database operations add/validate both FKs
only after the target unique constraint is attached, and state operations
replace the two temporary UUID fields. Do not let Django's autodetector create
an earlier implicit FK or a second physical column.

Repeat the same `NOT VALID` → `VALIDATE CONSTRAINT` sequence for
`users_pre_auth_challenges`, `users_mfa_credentials`, `users_backup_codes`,
and `users_recovery_tokens`. `IdentitySecurityAuditRecord.identity_id` remains
a nullable UUID snapshot rather than an FK so retained evidence survives any
future anonymization/purge workflow.

Every manual database operation in `0004` has an exact reverse. Reverse the
foreign keys and retained checks by name, drop each contracted `NOT NULL`,
drop attached unique constraints (which also drops their owned indexes), and
use `DROP INDEX CONCURRENTLY` only for the two independent non-unique indexes.
`SeparateDatabaseAndState` supplies matching reversible state operations.
Because the migration is `atomic=False`, forwards and backwards must not wrap
concurrent index DDL in a transaction and must be safe after a fully completed
preceding direction; partial-production repair is a separate runbook action.

Create `test_identity_migration_reversibility.py` with two distinct
PostgreSQL `MigrationExecutor` scenarios. The empty-schema scenario proves
pure DDL reversibility and FK sequencing; the populated scenario explicitly
runs the historical backfill and contract preflight before `0004`. Neither
helper may import runtime ORM models when `apps_registry` is supplied:

```python
import uuid

import pytest
from django.db.migrations.executor import MigrationExecutor

from apps.users.management.commands.preflight_identity_contract import (
    run_identity_contract_preflight,
)
from apps.users.tasks.identity_backfill import run_identity_backfill_batch


V2 = [("users", "0002_user_device_fingerprint_user_mfa_enabled_and_more")]
V3 = [("users", "0003_identity_expand")]
V4 = [("users", "0004_identity_contract_constraints")]


def migrate(connection, target):
    executor = MigrationExecutor(connection)
    executor.migrate(target)
    return MigrationExecutor(connection).loader.project_state(target).apps


@pytest.mark.django_db(transaction=True)
def test_empty_identity_ddl_round_trip_and_fk_sequence(iam_migration_connection):
    connection = iam_migration_connection
    migrate(connection, V2)
    migrate(connection, V3)
    assert_no_fk_targets_users_identity_id(connection)

    migrate(connection, V4)
    assert_identity_unique_constraint_precedes_validated_fks(connection)
    assert_validated_identity_fks_include(
        connection,
        "users_merge_source_identity_fk",
        "users_merge_canonical_identity_fk",
    )

    migrate(connection, V3)
    assert_contract_indexes_fks_and_not_null_are_absent(connection)
    migrate(connection, V2)
    assert_identity_tables_policies_triggers_and_functions_are_absent(connection)


@pytest.mark.django_db(transaction=True)
def test_populated_backfill_aware_round_trip_preserves_legacy_rows(
    iam_migration_connection,
):
    connection = iam_migration_connection
    v2_apps = migrate(connection, V2)
    LegacyUser = v2_apps.get_model("users", "User")
    tenant_id = uuid.uuid4()
    legacy = LegacyUser.objects.using(connection.alias).create(
        username="legacy-round-trip",
        email="legacy@example.com",
        password="pbkdf2_sha256$legacy-hash-snapshot",
        tenant_id=tenant_id,
        mfa_enabled=True,
        mfa_secret="legacy-secret-snapshot",
        device_fingerprint="legacy-device-snapshot",
    )
    expected = {
        "pk": legacy.pk,
        "username": legacy.username,
        "email": legacy.email,
        "password": legacy.password,
        "tenant_id": legacy.tenant_id,
        "mfa_enabled": legacy.mfa_enabled,
        "mfa_secret": legacy.mfa_secret,
        "device_fingerprint": legacy.device_fingerprint,
    }

    v3_apps = migrate(connection, V3)
    while True:
        result = run_identity_backfill_batch(
            apps_registry=v3_apps,
            using=connection.alias,
        )
        if result.processed_candidates == 0 and result.contended_candidates == 0:
            break
    run_identity_contract_preflight(
        apps_registry=v3_apps,
        using=connection.alias,
        inventory=historical_inventory(v3_apps, connection.alias),
    )

    migrate(connection, V4)
    assert_identity_unique_constraint_precedes_validated_fks(connection)
    migrate(connection, V3)
    assert_contract_indexes_fks_and_not_null_are_absent(connection)
    migrate(connection, V2)
    assert_identity_tables_policies_triggers_and_functions_are_absent(connection)
    LegacyUser = MigrationExecutor(connection).loader.project_state(V2).apps.get_model(
        "users",
        "User",
    )
    restored = LegacyUser.objects.using(connection.alias).values(
        *expected.keys(),
    ).get(pk=legacy.pk)
    assert restored == expected
```

The isolated schema fixture owns cleanup; this test never computes or migrates
to the repository leaf (which is `0005` after Task 17). The catalog helpers
assert the complete named sets from `0003`/`0004`: no leftover RLS
policies, non-internal triggers, trigger functions, concurrent indexes, FKs,
checks or temporary constraints. The historical backfill/preflight receive
both the `Apps` registry and database alias and use `.using(using)` throughout;
they must not import `apps.users.models` or otherwise touch the default test
schema. Add a third data-dependent rollback-floor case: after inserting a
valid `APPROVED_DEFERRED` operation at `0003`, reverse to `0002` raises
`IrreversibleError` before any DDL, while the clean and pending-only fixtures
above remain reversible. Add linter unit cases proving a security
`RunSQL` without `reverse_sql` fails and both migrations pass when all reverse
operations are present.

- [ ] **Step 5: Run contract checks**

Before running the repository linter, replace its regex-only `AlterField`
classification with an AST check that allows `migrations.AlterField` only when
it is nested in `SeparateDatabaseAndState(state_operations=[...])`. It must
still reject any database `AlterField`.

Add `infra/scripts/tests/test_migration_linter.py`:

```python
import unittest

from infra.scripts.migration_linter import find_destructive_operations


class MigrationLinterTests(unittest.TestCase):
    def test_state_only_alter_field_is_allowed(self):
        source = """
operations = [
    migrations.SeparateDatabaseAndState(
        database_operations=[migrations.RunSQL("ALTER TABLE example VALIDATE CONSTRAINT c")],
        state_operations=[migrations.AlterField(model_name="user", name="identity_id", field=models.UUIDField(unique=True))],
    ),
]
"""
        self.assertEqual(find_destructive_operations(source), [])

    def test_database_alter_field_is_rejected(self):
        source = """
operations = [
    migrations.AlterField(model_name="user", name="identity_id", field=models.UUIDField(unique=True)),
]
"""
        self.assertIn("AlterField", find_destructive_operations(source))
```

Then run:

```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py preflight_identity_contract
docker compose -f docker-compose.dev.yml exec backend python manage.py sqlmigrate users 0004
docker compose -f docker-compose.dev.yml run --rm --no-deps -v "$PWD:/workspace:ro" -w /workspace --entrypoint python backend infra/scripts/migration_linter.py
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_identity_contract_constraints.py apps/users/tests/test_identity_migration_reversibility.py -v
make agent-infra-test
```

Expected for IAM: contract tests pass and the linter output contains no `backend/apps/users/migrations/0004_identity_contract_constraints.py` failure. The known unrelated Requests baseline remains separately owned. Do not apply this migration in production until Release N+1 reconciliation approval exists.

- [ ] **Step 6: Commit the separately deployable contract release**

```bash
git add backend/apps/users/models.py backend/apps/users/models_identity.py backend/apps/users/models_membership.py backend/apps/users/migrations/0004_identity_contract_constraints.py backend/apps/users/management/commands/preflight_identity_contract.py backend/apps/users/tests/test_identity_contract_constraints.py backend/apps/users/tests/test_identity_migration_reversibility.py infra/scripts/migration_linter.py infra/scripts/tests/test_migration_linter.py backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py
git commit -m "feat(iam): enforce identity contract constraints"
```

---

### Task 5: Specialized Security Repositories and Integrity Checks

**Files:**

- Create: `backend/apps/users/repositories/identity_security_repository.py`
- Create: `backend/apps/users/repositories/preauth_repository.py`
- Create: `backend/apps/users/repositories/session_repository.py`
- Create: `backend/apps/users/repositories/mfa_recovery_repository.py`
- Create: `backend/apps/users/services/identity_security_service.py`
- Modify: `backend/apps/users/repositories/membership_repository.py`
- Modify: `backend/apps/users/security/identity_merge_locking.py`
- Modify: `backend/apps/users/services/deferred_merge_guard.py`
- Modify: `backend/apps/users/services/deferred_merge_credential_synchronizer.py`
- Test: `backend/apps/users/tests/test_identity_repositories.py`
- Test: `backend/apps/users/tests/test_deferred_merge_convergence.py`

**Interfaces:**

- Produces the only public global invalidation operations:

```python
def revoke_all_sessions(identity_id: UUID, reason: str) -> int: ...


def invalidate_identity_security_context(
    identity_id: UUID,
    expected_security_version: int,
    reason: str,
) -> SecurityInvalidationResult: ...
```

  The first never changes `security_version`; the second performs exactly one
  version increment plus the all-session revoke in one transaction.
- Produces `IdentitySecurityRepository.cas_set_status(identity_id,
  expected_status, new_status, reason) -> None`; it never changes
  `security_version`. A global block service composes this status CAS with the
  sole invalidation operation in one outer transaction.
- Produces `IdentitySessionRepository.revoke(session_id, expected_version,
  reason) -> None` and an internal bulk revoke primitive that never increments
  Identity state.
- Produces explicit pre-auth, MFA/recovery and membership CAS methods. No `atomic_update(model_class, **fields)` generic API.
- Makes both global invalidation operations merge-graph aware. A canonical
  Identity with realized deferred/finalized source edges is invalidated only
  through the shared Task 2 lock coordinator, complete graph hold check and
  closed mutation-effect matrix. The no-merge fast path is the same algorithm
  over a one-Identity graph, not a bypass.

- [ ] **Step 1: Write failing CAS/legal-hold tests**

```python
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections
from django.utils import timezone

from apps.users.repositories.session_repository import IdentitySessionRepository
from apps.users.models import IamTenantRegistryEntry, TenantGrantSwitchOperation
from apps.users.services.identity_security_service import (
    invalidate_identity_security_context,
    revoke_all_sessions,
)


@pytest.mark.django_db(transaction=True)
def test_legal_hold_blocks_security_response_atomically(identity, identity_session):
    type(identity).objects.filter(pk=identity.pk).update(identity_legal_hold=True)
    type(identity_session).objects.filter(pk=identity_session.pk).update(is_legal_hold=True)

    with pytest.raises(PermissionDenied):
        invalidate_identity_security_context(
            identity.identity_id,
            expected_security_version=1,
            reason="credential_compromise",
        )

    identity.refresh_from_db()
    identity_session.refresh_from_db()
    assert identity.security_version == 1
    assert identity_session.revoked_at is None


@pytest.mark.django_db(transaction=True)
def test_hold_commit_winning_concurrent_start_blocks_invalidation(
    identity,
    identity_session,
):
    start = Barrier(2)
    hold_committed = Event()

    def latch_hold():
        close_old_connections()
        try:
            start.wait()
            type(identity).objects.filter(pk=identity.pk).update(
                identity_legal_hold=True,
            )
            hold_committed.set()
        finally:
            close_old_connections()

    def invalidate_after_hold():
        close_old_connections()
        try:
            start.wait()
            assert hold_committed.wait(timeout=5)
            with pytest.raises(PermissionDenied):
                invalidate_identity_security_context(
                    identity.identity_id,
                    expected_security_version=1,
                    reason="credential_compromise",
                )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda fn: fn(), (latch_hold, invalidate_after_hold)))

    identity.refresh_from_db()
    identity_session.refresh_from_db()
    assert identity.security_version == 1
    assert identity_session.revoked_at is None


@pytest.mark.django_db(transaction=True)
def test_revoke_all_sessions_does_not_change_security_version(
    identity,
    identity_session,
):
    type(identity).objects.filter(pk=identity.pk).update(security_version=7)

    assert revoke_all_sessions(identity.identity_id, reason="operator_cleanup") == 1

    identity.refresh_from_db()
    identity_session.refresh_from_db()
    assert identity.security_version == 7
    assert identity_session.revoked_at is not None


@pytest.mark.django_db(transaction=True)
def test_identity_invalidation_increments_exactly_once_from_non_default_version(
    identity,
    identity_session,
):
    type(identity).objects.filter(pk=identity.pk).update(security_version=7)
    type(identity_session).objects.filter(pk=identity_session.pk).update(
        security_version_snapshot=7,
    )
    target_tenant_id = uuid.uuid4()
    IamTenantRegistryEntry.objects.create(
        tenant_id=target_tenant_id,
        source_code="security_test",
    )
    operation = TenantGrantSwitchOperation.objects.create(
        operation_id=uuid.uuid4(),
        session=identity_session,
        request_fingerprint="0" * 64,
        target_tenant_id=target_tenant_id,
        response_ciphertext=b"encrypted-bearer-result",
        response_nonce=b"123456789012",
        response_key_id="test-switch-key",
        response_expires_at=timezone.now() + timedelta(minutes=10),
    )

    result = invalidate_identity_security_context(
        identity.identity_id,
        expected_security_version=7,
        reason="logout_all",
    )

    identity.refresh_from_db()
    operation.refresh_from_db()
    assert result.security_version == 8
    assert identity.security_version == 8
    assert operation.response_ciphertext is None
    assert operation.response_nonce is None
    assert operation.response_key_id == ""


@pytest.mark.django_db(transaction=True)
def test_stale_session_cas_fails(identity_session):
    with pytest.raises(SessionCasConflict):
        IdentitySessionRepository.revoke(identity_session.id, expected_version=99, reason="test")
```

Also add `test_deferred_graph_hold_blocks_global_invalidation_atomically` for
each hold location `operation`, `source`, `canonical`, `session` and tenant
membership. It snapshots canonical/source security fields and every session,
invokes global invalidation by the canonical Identity, and asserts no mutable
row changes plus exactly one append-only `IdentityMergeHoldBlock` for the
action digest. Replaying the same denied action is evidence-idempotent.

- [ ] **Step 2: Run and verify failure**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_identity_repositories.py apps/users/tests/test_deferred_merge_convergence.py -v
```

- [ ] **Step 3: Implement focused repository methods**

Each repository method filters by stable IDs and expected version, updates
`version=F("version")+1`, and raises its domain-specific conflict exception
when `updated != 1`. The raw Identity increment is private to
`invalidate_identity_security_context()`; credential, MFA and logout services
must not call it directly.

```python
class IdentitySecurityRepository:
    @staticmethod
    def _cas_increment_security_version(
        identity_id: uuid.UUID,
        *,
        expected_version: int,
        reason: str,
    ) -> int:
        updated = User.objects.filter(
            identity_id=identity_id,
            security_version=expected_version,
            identity_legal_hold=False,
        ).update(security_version=F("security_version") + 1)
        if updated != 1:
            if User.objects.filter(
                identity_id=identity_id,
                identity_legal_hold=True,
            ).exists():
                raise PermissionDenied("LEGAL_HOLD_ACTIVE")
            raise IdentitySecurityCasConflict(identity_id)
        return expected_version + 1
```

`revoke_all_sessions()` uses the same coordinator and full graph/hold recheck,
then revokes every still-open canonical/source `IdentitySession` and
cryptoshreds live `TenantGrantSwitchOperation` response
ciphertext/nonce/key IDs for those sessions. It does not mutate
`User.security_version`, password, MFA, status or credential epoch. Individual
session logout remains scoped and does not expand to the graph.

Define the returned value in `identity_security_service.py`:

```python
@dataclass(frozen=True, slots=True)
class SecurityInvalidationResult:
    security_version: int
    revoked_session_count: int
    session_family_ids: tuple[uuid.UUID, ...]
```

`invalidate_identity_security_context()` opens one `transaction.atomic()` on
primary and delegates participant discovery/locking to
`IdentityMergeLockCoordinator`. It locks the canonical Identity, all realized
deferred/finalized source shadows, merge operations, sessions, switch
operations and tenant rows in the global order, then rejects any legal hold
before changing authority. Only after the complete locked re-read does it
require the exact canonical version, call the private CAS increment once,
apply the closed synchronizer effect for the reason, invoke the same bulk
session revoke primitive and return the new version plus affected
session-family IDs for post-commit Redis cleanup. CAS predicates include every
applicable hold/version/epoch latch so a concurrent hold or graph expansion
cannot race the precheck. A hold, stale expected version or expanded graph
rolls back every effect; the hold path appends one idempotent observation
outside the rolled-back mutation transaction. Global block/unblock paths that
invalidate credentials use this operation; individual logout and tenant-local
membership/grant revoke use only their scoped revoke methods, with the same
mandatory hold predicate.

No IAM repository bypasses legal hold. A held reconciliation row stays
unresolved and is escalated without changing DB or Redis authority; deployment
gates cannot waive it. Retention and envelope cryptoshredding also skip held
rows until the authorized legal-hold release workflow completes.
Every concrete CAS includes the hold predicate in the write itself:
`User.identity_legal_hold=False`, global security-record
`is_legal_hold=False`, and tenant model `is_legal_hold=False`. Raw SQL in the
RLS-safe grant switch carries the same predicate for the current grant,
membership and new-operation authority. Tests coordinate a hold writer and an
invalidation writer; when the hold transaction commits first, invalidation
returns typed 403/conflict and Identity version, sessions, grants and recovery
rows remain byte-for-byte unchanged.

- [ ] **Step 4: Add grant/membership integrity query helpers**

`MembershipRepository.require_enabled(tenant_id, identity_id)` always filters both values and `is_deleted=False`. Grant creation callers must assert:

```python
if membership.tenant_id != target_tenant_id:
    raise GrantIntegrityError("GRANT_MEMBERSHIP_TENANT_MISMATCH")
if session.identity_id != membership.identity_id:
    raise GrantIntegrityError("GRANT_SESSION_IDENTITY_MISMATCH")
```

- [ ] **Step 5: Run tests**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_identity_repositories.py apps/users/tests/test_deferred_merge_convergence.py apps/users/tests/test_identity_contract_constraints.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/users/repositories backend/apps/users/services/identity_security_service.py backend/apps/users/security/identity_merge_locking.py backend/apps/users/services/deferred_merge_guard.py backend/apps/users/services/deferred_merge_credential_synchronizer.py backend/apps/users/tests/test_identity_repositories.py backend/apps/users/tests/test_deferred_merge_convergence.py backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py
git commit -m "feat(iam): add specialized security repositories"
```

---

### Task 6: Pre-Auth Challenge and Mandatory MFA Orchestration

**Files:**

- Create: `backend/apps/users/services/auth_errors.py`
- Create: `backend/apps/users/services/auth_token_codec.py`
- Create: `backend/apps/users/services/preauth_service.py`
- Reuse: `backend/apps/users/services/deferred_merge_guard.py`
- Modify: `backend/apps/users/security/credential_entrypoints.py`
- Modify: `backend/apps/users/security/credential_entrypoint_manifest.json`
- Test: `backend/apps/users/tests/test_preauth_service.py`
- Test: `backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py`

**Interfaces:**

- Produces `verify_password(identifier, password, device_metadata) -> PasswordVerified`.
- Produces `verify_mfa(mfa_partial_token, factor_code, verifier) -> MfaVerified`.
- Produces `begin_mfa_setup(mfa_setup_partial_token, enrollment) -> MfaSetup`.
- Produces `complete_mfa_setup(mfa_setup_partial_token, factor_code, enrollment) -> MfaSetupVerified`.
- Produces signed `MFA_PARTIAL` or `MFA_SETUP_PARTIAL` with five-minute TTL.
- Produces signed one-time `TENANT_SELECTION` token after MFA.
- Consumes `MfaFactorVerifier.verify(identity_id, code) -> MfaFactorKind`; Task 9 supplies the production implementation.
- Consumes `MfaEnrollment.begin(identity_id, challenge_id) -> MfaSetup` and
  `MfaEnrollment.confirm(identity_id, challenge_id, code) ->
  MfaEnrollmentResult`; Task 9 supplies the production implementation.
- Produces one closed `AuthFailureCode` enum, exhaustive HTTP policy and
  `AuthFlowError(code: AuthFailureCode, retry_after_seconds: int | None =
  None)`. No call site may choose an HTTP status.
- Replaces Task 2's legacy manifest entries for password/MFA verification and
  enrollment with these exact guarded call sites. The manifest digest/build
  attestation must be regenerated and re-signed before merge execution is
  effective on this artifact.
- Calls `DeferredMergeGuard.require_activation_allowed()` immediately before
  MFA setup confirmation can change canonical status to `ACTIVE`.

- [ ] **Step 1: Write mandatory-flow tests with a fake factor verifier**

```python
import pytest

from apps.users.models import IdentitySession, PreAuthChallenge, TenantSessionGrant, User
from apps.users.services.auth_errors import AuthFlowError
from apps.users.services.preauth_service import (
    LoginIdentifier,
    begin_mfa_setup,
    complete_mfa_setup,
    verify_mfa,
    verify_password,
)


@pytest.fixture(autouse=True)
def auth_signing_keyring(settings):
    settings.AUTH_ACTIVE_SIGNING_KEY_ID = "test-v1"
    settings.AUTH_SIGNING_KEYRING = {"test-v1": "0123456789abcdef0123456789abcdef"}


@pytest.mark.django_db(transaction=True)
def test_password_verification_creates_only_five_minute_challenge(identity_with_confirmed_factor):
    identity = identity_with_confirmed_factor
    result = verify_password(
        LoginIdentifier(kind="email", value=identity.normalized_email),
        "Pass1!",
        {"browser": "test"},
    )

    assert result.token_type == "MFA_PARTIAL"
    assert 0 < (result.expires_at - result.issued_at).total_seconds() <= 300
    assert PreAuthChallenge.objects.filter(id=result.challenge_id).exists()
    assert not IdentitySession.objects.filter(identity_id=identity.identity_id).exists()
    assert not TenantSessionGrant.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_identity_without_factor_enters_setup_partial_without_session(identity_without_factor):
    identity = identity_without_factor
    User.objects.filter(pk=identity.pk).update(
        security_status=User.SecurityStatus.MFA_SETUP_REQUIRED,
    )

    result = verify_password(
        LoginIdentifier(kind="email", value=identity.normalized_email),
        "Pass1!",
        {"browser": "test"},
    )

    assert result.token_type == "MFA_SETUP_PARTIAL"
    challenge = PreAuthChallenge.objects.get(id=result.challenge_id)
    assert challenge.flow_kind == PreAuthChallenge.FlowKind.MFA_SETUP
    assert not IdentitySession.objects.filter(identity_id=identity.identity_id).exists()
    assert not TenantSessionGrant.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_setup_confirmation_returns_ten_backup_codes_and_selection_only(
    identity_without_factor,
    fake_mfa_enrollment,
):
    identity = identity_without_factor
    User.objects.filter(pk=identity.pk).update(
        security_status=User.SecurityStatus.MFA_SETUP_REQUIRED,
    )
    partial = verify_password(
        LoginIdentifier(kind="email", value=identity.normalized_email),
        "Pass1!",
        {},
    )

    setup = begin_mfa_setup(partial.token, fake_mfa_enrollment)
    result = complete_mfa_setup(
        partial.token,
        fake_mfa_enrollment.current_code(setup),
        fake_mfa_enrollment,
    )

    assert result.token_type == "TENANT_SELECTION"
    assert len(result.backup_codes) == 10
    assert len(set(result.backup_codes)) == 10
    assert not hasattr(result, "access_token")
    assert not IdentitySession.objects.filter(identity_id=identity.identity_id).exists()
    assert not TenantSessionGrant.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_setup_partial_cannot_call_normal_mfa_verify(
    identity_without_factor,
    accepting_factor_verifier,
):
    partial = verify_password(
        LoginIdentifier(
            kind="email",
            value=identity_without_factor.normalized_email,
        ),
        "Pass1!",
        {},
    )
    with pytest.raises(AuthFlowError, match="AUTH_TOKEN_TYPE_INVALID"):
        verify_mfa(partial.token, "123456", accepting_factor_verifier)


@pytest.mark.django_db(transaction=True)
def test_mfa_failure_does_not_issue_session_or_tenant_token(
    identity_with_confirmed_factor,
    rejecting_factor_verifier,
):
    identity = identity_with_confirmed_factor
    partial = verify_password(
        LoginIdentifier(kind="email", value=identity.normalized_email),
        "Pass1!",
        {},
    )

    with pytest.raises(AuthFlowError, match="MFA_CODE_INVALID"):
        verify_mfa(partial.token, "000000", rejecting_factor_verifier)

    assert not IdentitySession.objects.filter(identity_id=identity.identity_id).exists()


@pytest.mark.django_db(transaction=True)
def test_successful_mfa_returns_selection_context_not_access_token(
    identity_with_confirmed_factor,
    accepting_factor_verifier,
):
    identity = identity_with_confirmed_factor
    partial = verify_password(
        LoginIdentifier(kind="email", value=identity.normalized_email),
        "Pass1!",
        {},
    )
    result = verify_mfa(partial.token, "123456", accepting_factor_verifier)

    assert result.token_type == "TENANT_SELECTION"
    assert not hasattr(result, "access_token")
    assert not IdentitySession.objects.filter(identity_id=identity.identity_id).exists()


@pytest.mark.django_db(transaction=True)
def test_dormant_deferred_graph_cannot_activate_through_mfa_setup(
    dormant_deferred_merge_mfa_setup_partial,
    fake_mfa_enrollment,
):
    state = dormant_deferred_merge_mfa_setup_partial
    setup = begin_mfa_setup(state.partial_token, fake_mfa_enrollment)

    with pytest.raises(
        AuthFlowError,
        match="MERGE_DORMANT_FINALIZATION_REQUIRED",
    ):
        complete_mfa_setup(
            state.partial_token,
            fake_mfa_enrollment.current_code(setup),
            fake_mfa_enrollment,
        )

    state.challenge.refresh_from_db()
    state.operation.refresh_from_db()
    assert state.challenge.consumed_at is None
    assert state.operation.state == "approved_deferred"
```

- [ ] **Step 2: Run and verify failure**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_preauth_service.py -v
```

- [ ] **Step 3: Implement typed service failure and closed HTTP semantics**

```python
import uuid
from dataclasses import dataclass
from enum import StrEnum

from django.core.exceptions import PermissionDenied


class AuthFailureCode(StrEnum):
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    AUTH_TOKEN_TYPE_INVALID = "AUTH_TOKEN_TYPE_INVALID"
    MFA_CODE_INVALID = "MFA_CODE_INVALID"
    BACKUP_CODE_USED = "BACKUP_CODE_USED"
    RECOVERY_TOKEN_INVALID = "RECOVERY_TOKEN_INVALID"
    RECOVERY_TOKEN_USED = "RECOVERY_TOKEN_USED"
    REFRESH_COOKIE_REQUIRED = "REFRESH_COOKIE_REQUIRED"
    REFRESH_REUSE_DETECTED = "REFRESH_REUSE_DETECTED"
    SESSION_REVOKED = "SESSION_REVOKED"
    IDENTITY_SESSION_REVOKED = "IDENTITY_SESSION_REVOKED"
    IDENTITY_CLAIMS_MISMATCH = "IDENTITY_CLAIMS_MISMATCH"
    TENANT_CONTEXT_MISMATCH = "TENANT_CONTEXT_MISMATCH"
    IDENTITY_SECURITY_VERSION_STALE = "IDENTITY_SECURITY_VERSION_STALE"
    AUTHZ_VERSION_STALE = "AUTHZ_VERSION_STALE"
    POLICY_VERSION_STALE = "POLICY_VERSION_STALE"
    TENANT_SWITCHED = "TENANT_SWITCHED"
    TENANT_SELECTION_EXPIRED = "TENANT_SELECTION_EXPIRED"
    AUTH_ORIGIN_REJECTED = "AUTH_ORIGIN_REJECTED"
    CSRF_FAILED = "CSRF_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    MEMBERSHIP_DISABLED = "MEMBERSHIP_DISABLED"
    NO_ENABLED_MEMBERSHIP = "NO_ENABLED_MEMBERSHIP"
    MFA_ALREADY_CONFIGURED = "MFA_ALREADY_CONFIGURED"
    MFA_SETUP_ALREADY_CONSUMED = "MFA_SETUP_ALREADY_CONSUMED"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    IDEMPOTENCY_RESULT_EXPIRED = "IDEMPOTENCY_RESULT_EXPIRED"
    AUTH_RATE_LIMITED = "AUTH_RATE_LIMITED"
    AUTH_RATE_LIMIT_UNAVAILABLE = "AUTH_RATE_LIMIT_UNAVAILABLE"
    SESSION_RECONCILIATION_REQUIRED = "SESSION_RECONCILIATION_REQUIRED"
    TENANT_SWITCH_RECONCILIATION_REQUIRED = "TENANT_SWITCH_RECONCILIATION_REQUIRED"
    AUTH_DEPENDENCY_UNAVAILABLE = "AUTH_DEPENDENCY_UNAVAILABLE"
    PII_AUDIT_UNAVAILABLE = "PII_AUDIT_UNAVAILABLE"
    MFA_CONFIGURATION_INVALID = "MFA_CONFIGURATION_INVALID"
    MERGE_DORMANT_FINALIZATION_REQUIRED = "MERGE_DORMANT_FINALIZATION_REQUIRED"
    IDENTITY_INPUT_REQUIRED = "IDENTITY_INPUT_REQUIRED"
    AMBIGUOUS_IDENTITY_INPUT = "AMBIGUOUS_IDENTITY_INPUT"


@dataclass(frozen=True, slots=True)
class AuthFailurePolicy:
    status_code: int
    fixed_retry_after_seconds: int | None = None
    requires_dynamic_retry_after: bool = False


AUTHENTICATION_FAILURES = frozenset(
    {
        AuthFailureCode.INVALID_CREDENTIALS,
        AuthFailureCode.AUTH_TOKEN_INVALID,
        AuthFailureCode.AUTH_TOKEN_EXPIRED,
        AuthFailureCode.AUTH_TOKEN_TYPE_INVALID,
        AuthFailureCode.MFA_CODE_INVALID,
        AuthFailureCode.BACKUP_CODE_USED,
        AuthFailureCode.RECOVERY_TOKEN_INVALID,
        AuthFailureCode.RECOVERY_TOKEN_USED,
        AuthFailureCode.REFRESH_COOKIE_REQUIRED,
        AuthFailureCode.REFRESH_REUSE_DETECTED,
        AuthFailureCode.SESSION_REVOKED,
        AuthFailureCode.IDENTITY_SESSION_REVOKED,
        AuthFailureCode.IDENTITY_CLAIMS_MISMATCH,
        AuthFailureCode.TENANT_CONTEXT_MISMATCH,
        AuthFailureCode.IDENTITY_SECURITY_VERSION_STALE,
        AuthFailureCode.AUTHZ_VERSION_STALE,
        AuthFailureCode.POLICY_VERSION_STALE,
        AuthFailureCode.TENANT_SWITCHED,
        AuthFailureCode.TENANT_SELECTION_EXPIRED,
    }
)
REQUEST_OR_PERMISSION_FAILURES = frozenset(
    {
        AuthFailureCode.AUTH_ORIGIN_REJECTED,
        AuthFailureCode.CSRF_FAILED,
        AuthFailureCode.PERMISSION_DENIED,
        AuthFailureCode.MEMBERSHIP_DISABLED,
        AuthFailureCode.NO_ENABLED_MEMBERSHIP,
    }
)
CONFLICT_FAILURES = frozenset(
    {
        AuthFailureCode.MFA_ALREADY_CONFIGURED,
        AuthFailureCode.MFA_SETUP_ALREADY_CONSUMED,
        AuthFailureCode.MERGE_DORMANT_FINALIZATION_REQUIRED,
        AuthFailureCode.IDEMPOTENCY_KEY_REUSED,
        AuthFailureCode.IDEMPOTENCY_RESULT_EXPIRED,
    }
)
TRANSIENT_INFRASTRUCTURE_FAILURES = frozenset(
    {
        AuthFailureCode.AUTH_RATE_LIMIT_UNAVAILABLE,
        AuthFailureCode.SESSION_RECONCILIATION_REQUIRED,
        AuthFailureCode.TENANT_SWITCH_RECONCILIATION_REQUIRED,
        AuthFailureCode.AUTH_DEPENDENCY_UNAVAILABLE,
        AuthFailureCode.PII_AUDIT_UNAVAILABLE,
        AuthFailureCode.MFA_CONFIGURATION_INVALID,
    }
)
VALIDATION_FAILURES = frozenset(
    {
        AuthFailureCode.IDENTITY_INPUT_REQUIRED,
        AuthFailureCode.AMBIGUOUS_IDENTITY_INPUT,
    }
)


AUTH_FAILURE_POLICIES: dict[AuthFailureCode, AuthFailurePolicy] = {
    # Every enum member is listed exactly once in implementation. Groups are
    # expanded into this dict; there is no default/fallback status.
    **{code: AuthFailurePolicy(401) for code in AUTHENTICATION_FAILURES},
    **{code: AuthFailurePolicy(403) for code in REQUEST_OR_PERMISSION_FAILURES},
    **{code: AuthFailurePolicy(409) for code in CONFLICT_FAILURES},
    AuthFailureCode.AUTH_RATE_LIMITED: AuthFailurePolicy(
        429,
        requires_dynamic_retry_after=True,
    ),
    **{
        code: AuthFailurePolicy(503, fixed_retry_after_seconds=3)
        for code in TRANSIENT_INFRASTRUCTURE_FAILURES
    },
    **{code: AuthFailurePolicy(422) for code in VALIDATION_FAILURES},
}


def validate_retry_after(
    policy: AuthFailurePolicy,
    supplied: int | None,
) -> int | None:
    if policy.requires_dynamic_retry_after:
        if isinstance(supplied, bool) or not isinstance(supplied, int) or supplied <= 0:
            raise ValueError("positive Retry-After required for rate limit")
        return supplied
    if supplied is not None:
        raise ValueError("Retry-After override is not allowed for this code")
    return policy.fixed_retry_after_seconds


class AuthFlowError(PermissionDenied):
    def __init__(
        self,
        code: AuthFailureCode,
        *,
        retry_after_seconds: int | None = None,
    ):
        self.code = code
        self.retry_after_seconds = validate_retry_after(
            AUTH_FAILURE_POLICIES[code],
            retry_after_seconds,
        )
        super().__init__(code.value)
```

The named sets are closed constants. Authentication failures include invalid
credentials/token/recovery/refresh/session and stale-version codes. Request or
permission failures include Origin, CSRF, permission, disabled/no membership.
Conflicts include both idempotency failures and already-configured/consumed
setup. Transient infrastructure failures include limiter unavailable,
refresh/switch reconciliation, primary DB/auth dependency unavailable and MFA
configuration inconsistency. Validation failures contain only missing or
ambiguous login-identifier shapes. Add an assertion at import/test time:
`set(AUTH_FAILURE_POLICIES) == set(AuthFailureCode)`. Unknown strings and an
arbitrary `status_code=` are type/test failures, never implicit HTTP 401.

Only `AUTH_RATE_LIMITED` accepts a positive dynamic `retry_after_seconds`
computed from the limiter reset instant. Every temporary 503 uses the fixed
three-second policy. Other codes reject a retry override. Task 10's response
adapter is the only place that turns this policy into HTTP status and headers.

- [ ] **Step 4: Implement key-versioned signed token codec**

Use JWT header `kid`; allowed keys come from settings and the active key ID selects issuance:

```python
def encode_auth_token(payload: dict[str, object], *, token_type: str, ttl: timedelta) -> str:
    kid = settings.AUTH_ACTIVE_SIGNING_KEY_ID
    now = timezone.now()
    claims = {
        **payload,
        "iss": "identity-v1",
        "aud": "complect-api",
        "type": token_type,
        "iat": now,
        "exp": now + ttl,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(
        claims,
        settings.AUTH_SIGNING_KEYRING[kid],
        algorithm="HS256",
        headers={"kid": kid},
    )
```

Decoding passes `issuer="identity-v1"` and `audience="complect-api"` to PyJWT
and rejects missing/unknown `kid`, wrong `iss`/`aud`, wrong type, expiry and
signature failure.

- [ ] **Step 5: Implement password, MFA verification and MFA bootstrap transitions**

`verify_password()` accepts a typed `LoginIdentifier(kind, value)`.
`kind=email` performs the exact Task 2 normalization and lookup by
`normalized_email`. `kind=username` first performs the existing exact legacy
`username` lookup; if no username matches and the value is a syntactically
valid email, it falls back to normalized-email lookup for clients that
historically sent email in the username field. It then runs
`check_password()`, status/factor consistency checks and
`PreAuthChallengeRepository.issue()`. Invalid identifier and invalid password
both raise `INVALID_CREDENTIALS`.

- `ACTIVE` plus a confirmed factor issues `MFA_PARTIAL` with
  `flow_kind=MFA_VERIFY`.
- `MFA_SETUP_REQUIRED` plus no confirmed factor issues `MFA_SETUP_PARTIAL`
  with `flow_kind=MFA_SETUP`.
- `PENDING_CREDENTIALS` and `LOCKED` fail closed.
- `ACTIVE` without a confirmed factor, or `MFA_SETUP_REQUIRED` with a confirmed
  factor, raises `MFA_CONFIGURATION_INVALID`; it never silently changes status.

`verify_mfa()`:

1. decodes `MFA_PARTIAL`;
2. locks the challenge;
3. verifies expiry, `security_version` snapshot and attempt count;
4. calls the factor verifier;
5. marks `mfa_verified_at`;
6. returns `TENANT_SELECTION` containing challenge ID, identity ID and security-version snapshot.

`begin_mfa_setup()` accepts only an unexpired, unconsumed `MFA_SETUP_PARTIAL`
whose signed token type matches the locked challenge `flow_kind`. It asks the
Task 9 enrollment service to create or return the pending credential bound to
`(identity_id, challenge_id)`.

`complete_mfa_setup()` locks that same challenge and calls the Task 9 enrollment
CAS. The CAS confirms the TOTP, generates exactly ten backup codes, changes
`MFA_SETUP_REQUIRED -> ACTIVE` only after the Task 2 activation guard proves
there is no dormant deferred/ready merge edge, then calls the sole Task 5
`invalidate_identity_security_context()` operation with the locked expected
version (it normally revokes zero sessions at bootstrap) and returns exactly
`N+1`. The orchestration consumes the challenge and issues
`TENANT_SELECTION` using the new security-version snapshot. A replay cannot
return backup codes again.

None of these three transitions calls an access/refresh issuer or creates a
session/grant.

- [ ] **Step 6: Run tests**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_preauth_service.py apps/users/tests/test_deferred_merge_entrypoint_coverage.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/users/services/auth_errors.py backend/apps/users/services/auth_token_codec.py backend/apps/users/services/preauth_service.py backend/apps/users/security/credential_entrypoints.py backend/apps/users/security/credential_entrypoint_manifest.json backend/apps/users/tests/test_preauth_service.py backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py
git commit -m "feat(iam): require preauth mfa bootstrap"
```

---

### Task 7: IdentitySession and Refresh-Family Rotation

**Files:**

- Modify: `backend/apps/users/repositories/session_repository.py`
- Create: `backend/apps/users/services/refresh_rotation_service.py`
- Create: `backend/apps/users/services/session_service.py`
- Create: `backend/apps/users/services/session_reconciliation_service.py`
- Modify: `backend/apps/users/security/credential_entrypoints.py`
- Modify: `backend/apps/users/security/credential_entrypoint_manifest.json`
- Test: `backend/apps/users/tests/test_identity_session_service.py`
- Test: `backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py`

**Interfaces:**

- Produces
  `create_session(identity_id, security_version_snapshot, device_metadata, expected_mfa_challenge_id) -> IdentitySession`.
- Produces `issue_refresh_context(session, grant) -> RefreshIssue`; the value
  contains token, keyed `refresh_digest`, generation and signed current
  context, but does not mutate Redis by itself.
- Produces `rotate_refresh(redis_client, refresh_token) -> RefreshRotation`.
- Produces `apply_refresh_family_cas(redis_client, proof) ->
  RefreshFamilyCasResult` with the closed results `INITIALIZED`, `REPLAYED`,
  `ROTATED`, `REUSED` and `MISSING_UNEXPECTED`.
- Reuses Task 5 `revoke_all_sessions()` and
  `invalidate_identity_security_context()`; this task does not introduce a
  second owner of `security_version`.
- Produces `revoke_session(session_id, identity_id, reason)` and list-session
  queries.
- Registers refresh validation/rotation, session issue/revoke, logout and
  logout-all call sites in the closed Task 2 entry-point manifest, removes
  superseded legacy entries and requires a new exact-build attestation.

- [ ] **Step 1: Write failing refresh-family tests**

```python
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from django.db import close_old_connections
from django.utils import timezone

from apps.common.tenancy.context import clear_tenant_context, set_tenant_context
from apps.users.models import (
    IdentitySession,
    SessionReconciliationRecord,
    TenantGrantSwitchOperation,
    TenantSessionGrant,
)
from apps.users.services.auth_errors import AuthFlowError
from apps.users.services.session_service import (
    issue_refresh_context,
    logout_all,
    revoke_session,
)

from apps.users.services.refresh_rotation_service import (
    RefreshFamilyCasProof,
    RefreshFamilyCasResult,
    apply_refresh_family_cas,
    refresh_family_key,
    rotate_refresh,
)


@pytest.fixture
def active_session_context(identity, active_membership, redis):
    session = IdentitySession.objects.create(
        identity_id=identity.identity_id,
        security_version_snapshot=identity.security_version,
        expires_at=timezone.now() + timedelta(days=7),
    )
    set_tenant_context(active_membership.tenant_id)
    try:
        grant = TenantSessionGrant.objects.create(
            tenant_id=active_membership.tenant_id,
            session=session,
            membership=active_membership,
            context_version=1,
            issued_at=timezone.now(),
            expires_at=timezone.now() + timedelta(hours=8),
        )
    finally:
        clear_tenant_context()
    refresh_issue = issue_refresh_context(session, grant)
    operation_id = uuid.uuid4()
    IdentitySession.objects.filter(pk=session.pk).update(
        current_refresh_digest=refresh_issue.refresh_digest,
        refresh_generation=0,
    )
    TenantGrantSwitchOperation.objects.create(
        operation_id=operation_id,
        session=session,
        request_fingerprint="0" * 64,
        target_tenant_id=active_membership.tenant_id,
        status=TenantGrantSwitchOperation.Status.DB_COMMITTED,
        result_grant_id=grant.id,
        result_tenant_id=active_membership.tenant_id,
        result_context_version=grant.context_version,
        result_refresh_generation=0,
        result_refresh_digest=refresh_issue.refresh_digest,
    )
    cas_result = apply_refresh_family_cas(
        redis,
        RefreshFamilyCasProof(
            mode="initialize",
            session_id=session.id,
            session_family_id=session.session_family_id,
            operation_id=operation_id,
            expected_generation=0,
            expected_digest="",
            result_generation=0,
            result_digest=refresh_issue.refresh_digest,
            ttl_seconds=7 * 24 * 60 * 60,
        ),
    )
    assert cas_result is RefreshFamilyCasResult.INITIALIZED
    return SimpleNamespace(
        session=session,
        grant=grant,
        refresh_issue=refresh_issue,
    )


@pytest.mark.django_db(transaction=True)
def test_refresh_rotates_generation_and_preserves_signed_current_context(active_session_context, redis):
    issued = active_session_context.refresh_issue

    rotated = rotate_refresh(redis, issued.refresh_token)

    assert rotated.refresh_token != issued.refresh_token
    assert rotated.session_id == issued.session_id
    assert rotated.current_grant_id == issued.current_grant_id
    assert rotated.current_tenant_id == issued.current_tenant_id
    assert rotated.context_version == issued.context_version
    assert rotated.refresh_generation == issued.refresh_generation + 1


@pytest.mark.django_db(transaction=True)
def test_refresh_reuse_revokes_entire_session_family(active_session_context, redis):
    original = active_session_context.refresh_issue.refresh_token
    rotate_refresh(redis, original)

    with pytest.raises(AuthFlowError, match="REFRESH_REUSE_DETECTED"):
        rotate_refresh(redis, original)

    active_session_context.session.refresh_from_db()
    assert active_session_context.session.revoked_at is not None


@pytest.mark.django_db(transaction=True)
def test_refresh_rejects_session_security_snapshot_after_identity_change(
    active_session_context,
    identity,
    redis,
):
    type(identity).objects.filter(pk=identity.pk).update(
        security_version=identity.security_version + 1,
    )
    with pytest.raises(AuthFlowError, match="IDENTITY_SECURITY_VERSION_STALE"):
        rotate_refresh(redis, active_session_context.refresh_issue.refresh_token)


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_refreshes_have_one_success_then_reuse_revoke(
    active_session_context,
    redis,
):
    token = active_session_context.refresh_issue.refresh_token
    barrier = Barrier(2)

    def consume():
        close_old_connections()
        try:
            barrier.wait()
            try:
                return "success", rotate_refresh(redis, token)
            except AuthFlowError as exc:
                return "error", exc.code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume(), range(2)))

    assert [kind for kind, _ in results].count("success") == 1
    assert [value for kind, value in results if kind == "error"] == [
        "REFRESH_REUSE_DETECTED"
    ]
    active_session_context.session.refresh_from_db()
    assert active_session_context.session.revoked_at is not None


@pytest.mark.django_db(transaction=True)
def test_redis_failure_after_db_refresh_rotation_revokes_and_records(
    active_session_context,
    failing_redis,
):
    with pytest.raises(AuthFlowError, match="SESSION_RECONCILIATION_REQUIRED"):
        rotate_refresh(failing_redis, active_session_context.refresh_issue.refresh_token)

    active_session_context.session.refresh_from_db()
    assert active_session_context.session.revoked_at is not None
    record = SessionReconciliationRecord.objects.get(
        session_id=active_session_context.session.id,
        kind=SessionReconciliationRecord.Kind.REFRESH_ROTATION,
        switch_operation__isnull=True,
        resolved_at__isnull=True,
    )
    assert set(record.expected_db_state) == {
        "schema_version",
        "session_state",
        "refresh_generation",
        "refresh_digest",
        "grant_id",
    }
    assert record.expected_db_state["schema_version"] == 1
    assert record.expected_db_state["session_state"] == "rotation_committed"
    assert record.expected_db_state["grant_id"] == str(
        active_session_context.grant.id,
    )
    assert set(record.expected_redis_state) == {
        "schema_version",
        "family_state",
        "generation",
        "digest",
        "last_operation_id",
    }
    assert record.expected_redis_state["schema_version"] == 1


@pytest.mark.django_db(transaction=True)
def test_missing_redis_family_during_ordinary_refresh_fails_reconciliation(
    active_session_context,
    redis,
):
    redis.delete(refresh_family_key(active_session_context.session))

    with pytest.raises(AuthFlowError, match="SESSION_RECONCILIATION_REQUIRED"):
        rotate_refresh(redis, active_session_context.refresh_issue.refresh_token)

    active_session_context.session.refresh_from_db()
    assert active_session_context.session.revoked_at is not None


@pytest.mark.django_db(transaction=True)
def test_logout_all_redis_cleanup_records_non_switch_reconciliation(
    authenticated_identity,
    active_session_context,
    failing_redis,
):
    with pytest.raises(AuthFlowError, match="SESSION_RECONCILIATION_REQUIRED"):
        logout_all(
            authenticated_identity.identity_id,
            expected_security_version=authenticated_identity.security_version,
            redis_client=failing_redis,
        )

    record = SessionReconciliationRecord.objects.get(
        session_id=active_session_context.session.id,
        kind=SessionReconciliationRecord.Kind.SESSION_FAMILY_CLEANUP,
    )
    assert record.switch_operation_id is None
    assert record.expected_db_state["schema_version"] == 1
    assert record.expected_db_state["session_state"] == "revoked"
    assert set(record.expected_redis_state) == {
        "schema_version",
        "family_state",
        "generation",
        "digest",
        "last_operation_id",
    }


@pytest.mark.django_db(transaction=True)
def test_logout_all_increments_security_version_exactly_once(
    identity,
    active_session_context,
    redis,
):
    type(identity).objects.filter(pk=identity.pk).update(security_version=7)
    IdentitySession.objects.filter(pk=active_session_context.session.pk).update(
        security_version_snapshot=7,
    )

    logout_all(
        identity.identity_id,
        expected_security_version=7,
        redis_client=redis,
    )

    identity.refresh_from_db()
    active_session_context.session.refresh_from_db()
    assert identity.security_version == 8
    assert active_session_context.session.revoked_at is not None


@pytest.mark.django_db(transaction=True)
def test_individual_logout_keeps_security_version_unchanged(
    identity,
    active_session_context,
):
    type(identity).objects.filter(pk=identity.pk).update(security_version=7)
    IdentitySession.objects.filter(pk=active_session_context.session.pk).update(
        security_version_snapshot=7,
    )

    revoke_session(
        active_session_context.session.id,
        identity.identity_id,
        reason="user_request",
    )

    identity.refresh_from_db()
    active_session_context.session.refresh_from_db()
    assert identity.security_version == 7
    assert active_session_context.session.revoked_at is not None
```

- [ ] **Step 2: Run and verify failure**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_identity_session_service.py -v
```

- [ ] **Step 3: Implement signed refresh context**

Refresh claims contain:

```python
{
    "type": "REFRESH",
    "sub": str(identity_id),
    "identity_id": str(identity_id),
    "session_id": str(session_id),
    "session_family_id": str(session_family_id),
    "current_grant_id": str(grant_id),
    "current_tenant_id": str(tenant_id),
    "context_version": context_version,
    "refresh_generation": refresh_generation,
    "jti": str(jti),
}
```

Store only an HMAC digest of `jti` in DB and Redis.

- [ ] **Step 4: Implement the closed Redis refresh-family state machine**

Store one Redis hash per unique `session_family_id` with
`generation`, `digest` and `last_operation_id`. One Lua script receives mode,
expected state, result state, operation ID and TTL and returns exactly:

```python
class RefreshFamilyCasResult(StrEnum):
    INITIALIZED = "initialized"
    REPLAYED = "replayed"
    ROTATED = "rotated"
    REUSED = "reused"
    MISSING_UNEXPECTED = "missing_unexpected"


@dataclass(frozen=True, slots=True)
class RefreshFamilyCasProof:
    mode: Literal["initialize", "refresh", "switch"]
    session_id: uuid.UUID
    session_family_id: uuid.UUID
    operation_id: uuid.UUID
    expected_generation: int
    expected_digest: str
    result_generation: int
    result_digest: str
    ttl_seconds: int
```

```text
INITIALIZED
  key is absent
  mode == INITIALIZE
  expected_generation == 0
  result_generation == 0
  caller supplied a DB_COMMITTED operation proof whose session digest equals
  result_digest
  -> create the family hash

REPLAYED
  key already contains result_generation, result_digest and operation_id
  -> success without changing generation or TTL beyond the persisted expiry

ROTATED
  key contains expected_generation and expected_digest
  -> atomically replace them with result_generation/result_digest/operation_id

REUSED
  key exists but matches neither the exact replay tuple nor expected state
  -> revoke the affected IdentitySession family and cryptoshred its envelopes

MISSING_UNEXPECTED
  key is absent for REFRESH or SWITCH mode
  -> fail closed and persist reconciliation
```

Lua cannot prove database state. The Python adapter permits `INITIALIZE` only
after a primary transaction has locked the session plus a matching
`TenantGrantSwitchOperation(status=DB_COMMITTED)` and proven:
`session.refresh_generation == operation.result_refresh_generation == 0` and
`session.current_refresh_digest == operation.result_refresh_digest ==
proof.result_digest`. Initial auto-select and initial manual selection use this
path. No ordinary refresh/switch caller can set `mode=INITIALIZE`.

DB session generation/digest is the durable authority. Redis is the replay fast
path. The DB rotation is one conditional update on session ID, expected
generation and expected digest. Under a race exactly one update succeeds; the
loser is classified as reuse and revokes the session family. A
read-then-unconditional-update sequence is forbidden.

Before rotating, load the Identity from primary and require
`session.security_version_snapshot == identity.security_version ==`
the signed identity version. Then use the signed `current_tenant_id` to
establish RLS context and validate the exact `current_grant_id`, session ID,
open/expiry state and `context_version`. Never search for a current grant by
session alone.

For ordinary refresh, the DB conditional update changes generation `N -> N+1`
and digest exactly once before `ROTATED`; a missing Redis key is
`MISSING_UNEXPECTED`, never implicit initialization. If DB CAS rolls back or Redis fails after DB commit, catch outside the broken
transaction and call `record_session_failure_and_revoke()` in a new
transaction. Store expected DB/Redis digests and operation ID in
`SessionReconciliationRecord(kind=REFRESH_ROTATION,
switch_operation_id=None)`; do not issue replacement tokens.

- [ ] **Step 5: Add individual and global revoke paths**

`revoke_session()` filters by both `session_id` and caller `identity_id`; it is
idempotent, does not change `Identity.security_version`, and cryptoshreds all
live switch-response envelopes for that session in the same DB transaction.
Individual logout calls it. Refresh reuse revokes that affected global
`IdentitySession` family and clears its envelopes, but does not invalidate
other sessions.

Logout-all calls Task 5
`invalidate_identity_security_context(identity_id,
expected_security_version, reason="logout_all")`; it must never call a version
increment and a bulk-revoke helper separately. Because that operation is
merge-aware, logout-all locks and revokes every realized source/canonical
session family, increments canonical `security_version` once and applies only
the `LogoutAll` source epoch/session effects from the closed matrix. A hold on
any graph participant blocks the whole mutation and writes one append-only
hold observation. Tests set the starting version
to `N=7` and assert the exact transition to `8`, while individual logout
asserts it remains `7`. Redis family deletion happens after commit; a failure
creates one durable
`SessionReconciliationRecord(kind=SESSION_FAMILY_CLEANUP,
switch_operation_id=None)` per affected family. The session is already
revoked; this row asks the worker only to prove Redis deletion.

- [ ] **Step 6: Run tests**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_identity_session_service.py apps/users/tests/test_identity_repositories.py apps/users/tests/test_deferred_merge_entrypoint_coverage.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/users/repositories/session_repository.py backend/apps/users/services/refresh_rotation_service.py backend/apps/users/services/session_service.py backend/apps/users/services/session_reconciliation_service.py backend/apps/users/security/credential_entrypoints.py backend/apps/users/security/credential_entrypoint_manifest.json backend/apps/users/tests/test_identity_session_service.py backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py
git commit -m "feat(iam): add durable identity sessions"
```

---

### Task 8: RLS-Safe Grant Switch, Durable Operation and Reconciliation

**Files:**

- Create: `backend/apps/users/repositories/tenant_grant_switch_repository.py`
- Create: `backend/apps/users/repositories/session_reconciliation_repository.py`
- Create: `backend/apps/users/services/tenant_switch_service.py`
- Create: `backend/apps/users/services/switch_response_keyring.py`
- Modify: `backend/apps/users/services/auth_token_codec.py`
- Modify: `backend/apps/users/services/refresh_rotation_service.py`
- Modify: `backend/apps/users/services/session_reconciliation_service.py`
- Modify: `backend/apps/users/security/identity_merge_locking.py`
- Modify: `backend/apps/users/security/credential_entrypoints.py`
- Modify: `backend/apps/users/security/credential_entrypoint_manifest.json`
- Create: `backend/apps/users/tasks/session_reconciliation.py`
- Create: `backend/apps/users/tasks/switch_response_retention.py`
- Create: `backend/apps/users/management/commands/reconcile_identity_sessions.py`
- Modify: `backend/config/settings/base.py`
- Modify: `backend/apps/users/tests/conftest.py`
- Modify: `backend/apps/users/tests/iam_factories.py`
- Test: `backend/apps/users/tests/test_tenant_grant_switch_repository.py`
- Test: `backend/apps/users/tests/test_tenant_switch_service.py`
- Test: `backend/apps/users/tests/test_session_reconciliation.py`
- Test: `backend/apps/users/tests/test_identity_merge_locking.py`
- Test: `backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py`

**Interfaces:**

- Produces:

```python
class TenantGrantSwitchRepository:
    @classmethod
    def switch(
        cls,
        *,
        session_id: uuid.UUID,
        expected_current_grant_id: uuid.UUID | None,
        expected_current_tenant_id: uuid.UUID | None,
        expected_context_version: int | None,
        target_tenant_id: uuid.UUID,
        operation_id: uuid.UUID,
        prepare_response: SwitchResponseBuilder,
    ) -> GrantSwitchDbResult: ...
```

`SwitchResponseBuilder` is a pure callable
`(LockedSwitchClaims) -> PreparedSwitchResponse`; it receives only locked
scalar claims, returns access/refresh/CSRF plaintext plus refresh digest, and
cannot access ORM or Redis. Repository tests pass a deterministic fake;
`tenant_switch_service` passes the production token builder.

- Makes initial selection, switch, failure handling and reconciliation use the
  shared merge coordinator. No Task 8 path locks a switch operation, session,
  membership or grant before canonical User/merge participants. The global
  Task 8 subset rank is `IamTenantRegistryEntry -> PreAuthChallenge ->
  IdentitySession -> TenantGrantSwitchOperation ->
  SessionReconciliationRecord`; each sorted tenant locks
  `TenantAuthorizationState -> TenantMembership -> RoleBinding ->
  TenantSessionGrant`.
- Registers switch, refresh-context restore, failure-handler and reconciliation
  authority paths in the closed entry-point manifest and requires a fresh
  exact-build attestation for this artifact.

- Produces `complete_tenant_selection(selection_token, target_tenant_id,
  operation_id, redis_client) -> SessionTokens`.
- Produces `switch_tenant(current_context_token, target_tenant_id,
  operation_id, redis_client) -> SessionTokens`.
- Both service methods return the exact encrypted-envelope result for a replay
  of the same operation/fingerprint; neither can mint or rotate replacement
  tokens for a completed operation.
- Produces a narrow expired-selection replay decoder. Normal calls require a
  live five-minute token; the decoder may accept an expired but otherwise
  valid token only for an already-resulted operation inside the ten-minute
  response-envelope window.
- Produces `reconcile_identity_sessions(batch_size: int = 500) ->
  ReconciliationBatchResult` and the same-named management command for an
  operator-triggered pass.
- Produces three frozen source proofs; these are the only repository inputs:

```python
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class ReconciliationFailureCode(StrEnum):
    DB_TRANSITION_ROLLED_BACK = "db_transition_rolled_back"
    REDIS_CAS_FAILED = "redis_cas_failed"
    REDIS_FAMILY_MISSING = "redis_family_missing"
    REDIS_CLEANUP_FAILED = "redis_cleanup_failed"


class ReconciliationPayloadErrorCode(StrEnum):
    INVALID = "RECONCILIATION_PAYLOAD_INVALID"
    MISMATCH = "RECONCILIATION_PAYLOAD_MISMATCH"


class ReconciliationPayloadError(ValueError):
    def __init__(self, code: ReconciliationPayloadErrorCode):
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class ExpectedSessionDbState:
    schema_version: Literal[1]
    session_state: Literal["open", "revoked", "rotation_committed", "switch_committed"]
    refresh_generation: int | None
    refresh_digest: str | None
    grant_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class ExpectedRefreshRedisState:
    schema_version: Literal[1]
    family_state: Literal["present", "missing", "unknown"]
    generation: int | None
    digest: str | None
    last_operation_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class RefreshRotationReconciliationProof:
    operation_id: uuid.UUID
    expected_db_state: ExpectedSessionDbState
    expected_redis_state: ExpectedRefreshRedisState
    failure_code: ReconciliationFailureCode
    kind: Literal["refresh_rotation"] = "refresh_rotation"
    switch_operation_id: None = None


@dataclass(frozen=True, slots=True)
class TenantSwitchReconciliationProof:
    operation_id: uuid.UUID
    switch_operation_id: uuid.UUID
    expected_db_state: ExpectedSessionDbState
    expected_redis_state: ExpectedRefreshRedisState
    failure_code: ReconciliationFailureCode
    kind: Literal["tenant_switch"] = "tenant_switch"


@dataclass(frozen=True, slots=True)
class SessionFamilyCleanupProof:
    operation_id: uuid.UUID
    expected_db_state: ExpectedSessionDbState
    expected_redis_state: ExpectedRefreshRedisState
    failure_code: ReconciliationFailureCode
    kind: Literal["session_family_cleanup"] = "session_family_cleanup"
    switch_operation_id: None = None
```

For switch proof, `switch_operation_id == operation_id`. Cleanup proof requires
DB `session_state="revoked"` and Redis `family_state in {"present",
"unknown"}`. Refresh proof carries the committed DB generation/digest and the
expected Redis generation/digest. Repository serialization has an exact-key
schema per kind, validates non-negative generations and keyed hex digests, and
raises `ReconciliationPayloadError(MISMATCH)` when an existing
`(session_id, kind, operation_id)` row differs in any proof field.

- [ ] **Step 1: Write the required switch matrix**

Create tests for A→B, B→A, A→A replay, expired old grant, disabled target membership and exact signed old-context routing:

```python
from concurrent.futures import ThreadPoolExecutor
import hashlib
import uuid
from datetime import timedelta
from threading import Barrier

import pytest
from django.db import IntegrityError, close_old_connections
from django.utils import timezone

from apps.common.context.core import ContextCore, get_tenant_id
from apps.common.db.router import EnterpriseClusterRouter
from apps.common.tenancy.context import clear_tenant_context, set_tenant_context
from apps.users.models import (
    SessionReconciliationRecord,
    TenantGrantSwitchOperation,
    TenantMembership,
    TenantSessionGrant,
)
from apps.users.repositories.tenant_grant_switch_repository import TenantGrantSwitchRepository
from apps.users.services.auth_errors import AuthFlowError
from apps.users.services.tenant_switch_service import (
    FailClosedSwitchResult,
    record_failed_switch_and_revoke_session,
    switch_tenant,
)
from apps.users.services.switch_response_keyring import PreparedSwitchResponse
from apps.users.tests.iam_factories import (
    latch_identity_legal_hold,
    mark_switch_operation_completed,
)


def test_switch_response_builder(claims):
    refresh_token = f"refresh-{claims.grant_id}"
    return PreparedSwitchResponse(
        access_token=f"access-{claims.grant_id}",
        refresh_token=refresh_token,
        csrf_token=f"csrf-{claims.grant_id}",
        refresh_digest=hashlib.sha256(refresh_token.encode()).hexdigest(),
    )


@pytest.mark.django_db(transaction=True)
def test_switch_a_to_b_uses_signed_old_context(two_tenant_session):
    result = TenantGrantSwitchRepository.switch(
        session_id=two_tenant_session.session.id,
        expected_current_grant_id=two_tenant_session.grant_a.id,
        expected_current_tenant_id=two_tenant_session.tenant_a,
        expected_context_version=1,
        target_tenant_id=two_tenant_session.tenant_b,
        operation_id=uuid.uuid4(),
        prepare_response=test_switch_response_builder,
    )
    assert result.grant.tenant_id == two_tenant_session.tenant_b
    assert result.grant.context_version == 2


@pytest.mark.django_db(transaction=True)
def test_request_context_a_cannot_overwrite_repository_target_b(
    two_tenant_session,
    monkeypatch,
):
    def fail_if_router_is_used(*args, **kwargs):
        raise AssertionError("switch repository must bypass ORM router")

    monkeypatch.setattr(
        EnterpriseClusterRouter,
        "_inject_rls",
        fail_if_router_is_used,
    )
    set_tenant_context(two_tenant_session.tenant_a)
    try:
        result = switch_fixture_a_to_b(two_tenant_session)
        assert result.grant.tenant_id == two_tenant_session.tenant_b
        assert result.grant.membership_id == two_tenant_session.membership_b.id
        assert get_tenant_id() == two_tenant_session.tenant_a
    finally:
        clear_tenant_context()


@pytest.mark.django_db(transaction=True)
def test_expired_open_grant_is_superseded(
    two_tenant_session_with_expired_open_grant,
):
    scenario = two_tenant_session_with_expired_open_grant
    assert scenario.grant_a.expires_at <= timezone.now()

    result = switch_fixture_a_to_b(scenario)
    refresh_grant(scenario.grant_a, scenario.tenant_a)
    assert scenario.grant_a.superseded_at is not None
    assert result.grant.context_version == 2


@pytest.mark.django_db(transaction=True)
def test_a_to_a_operation_replay_returns_same_grant_without_second_increment(two_tenant_session):
    operation_id = uuid.uuid4()
    first = switch_fixture_a_to_a(two_tenant_session, operation_id=operation_id)
    second = switch_fixture_a_to_a(two_tenant_session, operation_id=operation_id)
    assert second.grant.id == first.grant.id
    assert second.grant.context_version == 2


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_switches_with_same_operation_return_one_grant(two_tenant_session):
    operation_id = uuid.uuid4()
    barrier = Barrier(2)

    def switch():
        close_old_connections()
        try:
            barrier.wait()
            return switch_fixture_a_to_b(
                two_tenant_session,
                operation_id=operation_id,
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: switch(), range(2)))

    assert {result.grant.id for result in results} == {results[0].grant.id}
    assert {result.grant.context_version for result in results} == {2}


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_switches_with_different_operations_allow_one_transition(
    two_tenant_session,
):
    barrier = Barrier(2)

    def switch(operation_id):
        close_old_connections()
        try:
            barrier.wait()
            try:
                return "success", switch_fixture_a_to_b(
                    two_tenant_session,
                    operation_id=operation_id,
                )
            except AuthFlowError as exc:
                return "error", exc.code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(switch, [uuid.uuid4(), uuid.uuid4()]))

    assert [kind for kind, _ in results].count("success") == 1
    assert [value for kind, value in results if kind == "error"] == [
        "TENANT_SWITCHED"
    ]
    set_tenant_context(two_tenant_session.tenant_b)
    try:
        assert TenantSessionGrant.objects.filter(
            session_id=two_tenant_session.session.id,
            superseded_at__isnull=True,
            revoked_at__isnull=True,
        ).count() == 1
    finally:
        clear_tenant_context()


@pytest.mark.django_db(transaction=True)
def test_switch_b_to_a(two_tenant_session_in_b):
    result = TenantGrantSwitchRepository.switch(
        session_id=two_tenant_session_in_b.session.id,
        expected_current_grant_id=two_tenant_session_in_b.grant_b.id,
        expected_current_tenant_id=two_tenant_session_in_b.tenant_b,
        expected_context_version=1,
        target_tenant_id=two_tenant_session_in_b.tenant_a,
        operation_id=uuid.uuid4(),
        prepare_response=test_switch_response_builder,
    )
    assert result.grant.tenant_id == two_tenant_session_in_b.tenant_a


@pytest.mark.django_db(transaction=True)
def test_disabled_target_membership_fails_closed(two_tenant_session):
    set_tenant_context(two_tenant_session.tenant_b)
    try:
        TenantMembership.objects.filter(pk=two_tenant_session.membership_b.id).update(
            status=TenantMembership.Status.DISABLED,
        )
    finally:
        clear_tenant_context()
    with pytest.raises(AuthFlowError, match="MEMBERSHIP_DISABLED"):
        switch_fixture_a_to_b(two_tenant_session)


def switch_fixture_a_to_b(context, *, operation_id=None):
    return TenantGrantSwitchRepository.switch(
        session_id=context.session.id,
        expected_current_grant_id=context.grant_a.id,
        expected_current_tenant_id=context.tenant_a,
        expected_context_version=1,
        target_tenant_id=context.tenant_b,
        operation_id=operation_id or uuid.uuid4(),
        prepare_response=test_switch_response_builder,
    )


def switch_fixture_a_to_a(context, *, operation_id):
    return TenantGrantSwitchRepository.switch(
        session_id=context.session.id,
        expected_current_grant_id=context.grant_a.id,
        expected_current_tenant_id=context.tenant_a,
        expected_context_version=1,
        target_tenant_id=context.tenant_a,
        operation_id=operation_id,
        prepare_response=test_switch_response_builder,
    )


def refresh_grant(grant, tenant_id):
    set_tenant_context(tenant_id)
    try:
        grant.refresh_from_db()
    finally:
        clear_tenant_context()
```

- [ ] **Step 2: Write DB rollback and Redis failure tests**

```python
def raise_db_error(*args, **kwargs):
    raise IntegrityError("forced insert failure")


@pytest.mark.django_db(transaction=True)
def test_db_failure_rolls_back_grant_then_records_fail_closed_in_new_transaction(
    two_tenant_session,
    redis,
    monkeypatch,
):
    monkeypatch.setattr(TenantGrantSwitchRepository, "_insert_grant", raise_db_error)
    operation_id = uuid.uuid4()

    with pytest.raises(AuthFlowError, match="TENANT_SWITCH_RECONCILIATION_REQUIRED"):
        switch_tenant(
            current_context_token=two_tenant_session.current_context_token,
            target_tenant_id=two_tenant_session.tenant_b,
            operation_id=operation_id,
            redis_client=redis,
        )

    refresh_grant(two_tenant_session.grant_a, two_tenant_session.tenant_a)
    two_tenant_session.session.refresh_from_db()
    assert two_tenant_session.grant_a.superseded_at is None
    assert two_tenant_session.session.revoked_at is not None
    record = SessionReconciliationRecord.objects.get(
        session_id=two_tenant_session.session.id,
        kind=SessionReconciliationRecord.Kind.TENANT_SWITCH,
        switch_operation__isnull=False,
    )
    assert record.operation_id == operation_id
    assert record.switch_operation_id == operation_id
    assert record.expected_db_state["schema_version"] == 1
    assert record.expected_db_state["session_state"] == "open"
    assert record.expected_redis_state["schema_version"] == 1


@pytest.mark.django_db(transaction=True)
def test_redis_failure_after_db_commit_keeps_transition_but_revokes_session(two_tenant_session, failing_redis):
    operation_id = uuid.uuid4()
    with pytest.raises(AuthFlowError, match="TENANT_SWITCH_RECONCILIATION_REQUIRED"):
        switch_tenant(
            current_context_token=two_tenant_session.current_context_token,
            target_tenant_id=two_tenant_session.tenant_b,
            operation_id=operation_id,
            redis_client=failing_redis,
        )

    refresh_grant(two_tenant_session.grant_a, two_tenant_session.tenant_a)
    two_tenant_session.session.refresh_from_db()
    assert two_tenant_session.grant_a.superseded_at is not None
    assert two_tenant_session.session.revoked_at is not None
    record = SessionReconciliationRecord.objects.get(
        session_id=two_tenant_session.session.id,
        kind=SessionReconciliationRecord.Kind.TENANT_SWITCH,
        switch_operation__isnull=False,
    )
    assert record.operation_id == operation_id
    assert record.switch_operation_id == operation_id
    assert record.expected_db_state["schema_version"] == 1
    assert record.expected_db_state["session_state"] == "switch_committed"
    assert record.expected_redis_state["schema_version"] == 1


@pytest.mark.django_db(transaction=True)
def test_failure_handler_loses_race_to_same_completed_operation_without_revoke(
    db_committed_switch_failure,
):
    completion_committed = Event()

    def complete_same_operation():
        close_old_connections()
        try:
            mark_switch_operation_completed(
                db_committed_switch_failure.operation_id,
            )
            completion_committed.set()
        finally:
            close_old_connections()

    def handle_stale_failure():
        close_old_connections()
        try:
            assert completion_committed.wait(timeout=5)
            return record_failed_switch_and_revoke_session(
                db_committed_switch_failure,
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        completed = pool.submit(complete_same_operation)
        handled = pool.submit(handle_stale_failure)
        completed.result()
        result = handled.result()

    assert result is FailClosedSwitchResult.ALREADY_COMPLETED
    db_committed_switch_failure.session.refresh_from_db()
    assert db_committed_switch_failure.session.revoked_at is None
    assert not SessionReconciliationRecord.objects.filter(
        operation_id=db_committed_switch_failure.operation_id,
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_legal_hold_commit_wins_race_before_fail_closed_handler(
    db_committed_switch_failure,
):
    hold_committed = Event()

    def latch_hold():
        close_old_connections()
        try:
            latch_identity_legal_hold(
                db_committed_switch_failure.session.identity_id,
            )
            hold_committed.set()
        finally:
            close_old_connections()

    def handle_after_hold():
        close_old_connections()
        try:
            assert hold_committed.wait(timeout=5)
            with pytest.raises(AuthFlowError, match="PERMISSION_DENIED"):
                record_failed_switch_and_revoke_session(
                    db_committed_switch_failure,
                )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        held = pool.submit(latch_hold)
        handled = pool.submit(handle_after_hold)
        held.result()
        handled.result()

    db_committed_switch_failure.session.refresh_from_db()
    db_committed_switch_failure.operation.refresh_from_db()
    assert db_committed_switch_failure.session.revoked_at is None
    assert db_committed_switch_failure.operation.status == (
        TenantGrantSwitchOperation.Status.DB_COMMITTED
    )
    assert not SessionReconciliationRecord.objects.filter(
        operation_id=db_committed_switch_failure.operation_id,
    ).exists()
```

Create `test_tenant_switch_service.py` with service-level idempotency tests:

```python
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.users.models import TenantGrantSwitchOperation
from apps.users.services.auth_errors import AuthFlowError
from apps.users.services.session_service import revoke_session
from apps.users.services.tenant_switch_service import (
    complete_auto_selection,
    complete_tenant_selection,
    resume_tenant_switch,
    switch_tenant,
)
from apps.users.tests.iam_factories import (
    expire_only_selection_token_clock,
    refresh_digest,
    redis_refresh_generation,
    redis_refresh_state,
    run_two_concurrent_switch_services,
    tenant_grant_count,
)


@pytest.mark.django_db(transaction=True)
def test_completed_replay_returns_byte_identical_token_response(
    two_tenant_session,
    redis,
):
    operation_id = uuid.uuid4()
    first = switch_tenant(
        two_tenant_session.current_context_token,
        two_tenant_session.tenant_b,
        operation_id,
        redis,
    )
    second = switch_tenant(
        two_tenant_session.current_context_token,
        two_tenant_session.tenant_b,
        operation_id,
        redis,
    )
    assert second == first
    operation = TenantGrantSwitchOperation.objects.get(
        operation_id=operation_id,
    )
    assert operation.response_ciphertext
    assert first.refresh_token.encode() not in operation.response_ciphertext
    assert operation.result_context_version == 2


@pytest.mark.django_db(transaction=True)
def test_replay_resumes_db_committed_operation_without_second_rotation(
    db_committed_switch_operation,
    redis,
):
    operation = db_committed_switch_operation
    assert operation.status == TenantGrantSwitchOperation.Status.DB_COMMITTED

    first_response = resume_tenant_switch(operation.operation_id, redis)
    replay = resume_tenant_switch(operation.operation_id, redis)

    assert replay == first_response
    assert redis_refresh_generation(operation.session_id) == 1


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_service_replays_return_one_exact_result(
    pending_switch_request,
    redis,
):
    results = run_two_concurrent_switch_services(
        pending_switch_request,
        redis,
    )
    assert results[0] == results[1]
    assert TenantGrantSwitchOperation.objects.filter(
        operation_id=pending_switch_request.operation_id,
    ).count() == 1
    assert redis_refresh_generation(pending_switch_request.session_id) == 1


@pytest.mark.django_db(transaction=True)
def test_expired_idempotency_envelope_never_rotates_again(
    completed_switch_operation,
    redis,
):
    TenantGrantSwitchOperation.objects.filter(
        pk=completed_switch_operation.pk,
    ).update(response_expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(AuthFlowError, match="IDEMPOTENCY_RESULT_EXPIRED"):
        resume_tenant_switch(completed_switch_operation.operation_id, redis)
    assert redis_refresh_generation(completed_switch_operation.session_id) == 1


@pytest.mark.django_db(transaction=True)
def test_expired_selection_token_replays_completed_result_until_envelope_expiry(
    completed_manual_selection,
    redis,
):
    expire_only_selection_token_clock(completed_manual_selection)

    replay = complete_tenant_selection(
        completed_manual_selection.selection_token,
        completed_manual_selection.target_tenant_id,
        completed_manual_selection.operation_id,
        redis,
    )

    assert replay == completed_manual_selection.original_response


@pytest.mark.django_db(transaction=True)
def test_expired_selection_cannot_create_or_resume_unresulted_operation(
    expired_selection_token,
    pending_selection_operation,
    redis,
):
    original_session_id = pending_selection_operation.session_id
    original_grant_count = tenant_grant_count(
        pending_selection_operation.target_tenant_id,
    )
    with pytest.raises(AuthFlowError, match="TENANT_SELECTION_EXPIRED"):
        complete_tenant_selection(
            expired_selection_token,
            pending_selection_operation.target_tenant_id,
            pending_selection_operation.operation_id,
            redis,
        )
    pending_selection_operation.refresh_from_db()
    assert pending_selection_operation.session_id == original_session_id
    assert pending_selection_operation.result_grant_id is None
    pending_selection_operation.session.refresh_from_db()
    assert pending_selection_operation.session.revoked_at is not None
    assert tenant_grant_count(
        pending_selection_operation.target_tenant_id,
    ) == original_grant_count


@pytest.mark.django_db(transaction=True)
def test_initial_manual_selection_initializes_missing_refresh_family(
    verified_multi_membership_selection,
    redis,
):
    result = complete_tenant_selection(
        verified_multi_membership_selection.token,
        verified_multi_membership_selection.target_tenant_id,
        verified_multi_membership_selection.operation_id,
        redis,
    )
    assert redis_refresh_state(result.session_id) == {
        "generation": 0,
        "digest": refresh_digest(result.refresh_token),
        "last_operation_id": str(verified_multi_membership_selection.operation_id),
    }


@pytest.mark.django_db(transaction=True)
def test_initial_auto_select_initializes_missing_refresh_family(
    verified_single_membership_challenge,
    redis,
):
    result = complete_auto_selection(verified_single_membership_challenge, redis)
    assert redis_refresh_state(result.session_id)["generation"] == 0


@pytest.mark.django_db(transaction=True)
def test_resume_initial_db_committed_operation_initializes_once(
    initial_db_committed_selection,
    redis,
):
    first = resume_tenant_switch(initial_db_committed_selection.operation_id, redis)
    second = resume_tenant_switch(initial_db_committed_selection.operation_id, redis)
    assert second == first
    assert redis_refresh_state(first.session_id)["generation"] == 0


@pytest.mark.django_db(transaction=True)
def test_completed_replay_never_returns_credentials_after_session_revoke(
    completed_switch_operation,
    redis,
):
    revoke_session(
        completed_switch_operation.session_id,
        completed_switch_operation.session.identity_id,
        reason="logout",
    )
    completed_switch_operation.refresh_from_db()
    assert completed_switch_operation.response_ciphertext is None
    with pytest.raises(AuthFlowError, match="SESSION_REVOKED"):
        resume_tenant_switch(completed_switch_operation.operation_id, redis)
```

Create `test_session_reconciliation.py`:

```python
from dataclasses import replace
import uuid

import pytest

from apps.users.models import (
    SessionReconciliationRecord,
    TenantGrantSwitchOperation,
)
from apps.users.repositories.session_reconciliation_repository import (
    ReconciliationFailureCode,
    ReconciliationPayloadError,
    ReconciliationPayloadErrorCode,
    SessionReconciliationRepository,
)
from apps.users.services.refresh_rotation_service import refresh_family_key
from apps.users.services.session_reconciliation_service import (
    reconcile_identity_sessions,
)


@pytest.mark.django_db(transaction=True)
def test_reconciler_revokes_and_closes_ambiguous_session(
    unresolved_reconciliation,
    redis,
):
    redis.set(refresh_family_key(unresolved_reconciliation.session), "orphan")

    result = reconcile_identity_sessions(batch_size=10, redis_client=redis)

    unresolved_reconciliation.refresh_from_db()
    unresolved_reconciliation.session.refresh_from_db()
    assert unresolved_reconciliation.kind == (
        SessionReconciliationRecord.Kind.TENANT_SWITCH
    )
    operation = unresolved_reconciliation.switch_operation
    assert result.resolved == 1
    assert unresolved_reconciliation.resolved_at is not None
    assert unresolved_reconciliation.session.revoked_at is not None
    assert operation.status == TenantGrantSwitchOperation.Status.FAILED
    assert not redis.exists(refresh_family_key(unresolved_reconciliation.session))


@pytest.mark.django_db(transaction=True)
def test_refresh_reconciler_does_not_require_switch_operation(
    refresh_reconciliation,
    redis,
):
    assert refresh_reconciliation.kind == (
        SessionReconciliationRecord.Kind.REFRESH_ROTATION
    )
    assert refresh_reconciliation.switch_operation_id is None
    redis.set(refresh_family_key(refresh_reconciliation.session), "stale")

    result = reconcile_identity_sessions(batch_size=10, redis_client=redis)

    refresh_reconciliation.refresh_from_db()
    refresh_reconciliation.session.refresh_from_db()
    assert result.resolved == 1
    assert refresh_reconciliation.resolved_at is not None
    assert refresh_reconciliation.session.revoked_at is not None
    assert not redis.exists(refresh_family_key(refresh_reconciliation.session))


@pytest.mark.django_db(transaction=True)
def test_session_family_cleanup_only_proves_redis_deletion(
    cleanup_reconciliation,
    redis,
):
    assert cleanup_reconciliation.kind == (
        SessionReconciliationRecord.Kind.SESSION_FAMILY_CLEANUP
    )
    assert cleanup_reconciliation.switch_operation_id is None
    assert cleanup_reconciliation.session.revoked_at is not None
    redis.set(refresh_family_key(cleanup_reconciliation.session), "orphan")

    result = reconcile_identity_sessions(batch_size=10, redis_client=redis)

    cleanup_reconciliation.refresh_from_db()
    assert result.resolved == 1
    assert cleanup_reconciliation.resolved_at is not None
    assert not redis.exists(refresh_family_key(cleanup_reconciliation.session))


@pytest.mark.django_db(transaction=True)
def test_reconciliation_record_is_idempotent_per_session_kind_operation(
    identity_session,
    valid_refresh_reconciliation_proof,
):
    proof = valid_refresh_reconciliation_proof.for_session(identity_session)
    for _ in range(2):
        SessionReconciliationRepository.create(
            session_id=identity_session.id,
            proof=proof,
        )
    assert SessionReconciliationRecord.objects.filter(
        session=identity_session,
        kind=SessionReconciliationRecord.Kind.REFRESH_ROTATION,
        operation_id=proof.operation_id,
    ).count() == 1


@pytest.mark.django_db(transaction=True)
def test_same_tuple_with_different_proof_is_rejected(
    identity_session,
    valid_refresh_reconciliation_proof,
):
    proof = valid_refresh_reconciliation_proof.for_session(identity_session)
    SessionReconciliationRepository.create(
        session_id=identity_session.id,
        proof=proof,
    )
    changed = replace(
        proof,
        failure_code=ReconciliationFailureCode.REDIS_FAMILY_MISSING,
    )
    with pytest.raises(ReconciliationPayloadError) as caught:
        SessionReconciliationRepository.create(
            session_id=identity_session.id,
            proof=changed,
        )
    assert caught.value.code is ReconciliationPayloadErrorCode.MISMATCH


@pytest.mark.django_db(transaction=True)
def test_same_session_and_operation_id_remain_distinct_across_kinds(
    revoked_identity_session,
    valid_refresh_reconciliation_proof,
    valid_cleanup_reconciliation_proof,
):
    operation_id = uuid.uuid4()
    refresh_proof = replace(
        valid_refresh_reconciliation_proof.for_session(
            revoked_identity_session,
        ),
        operation_id=operation_id,
    )
    cleanup_proof = replace(
        valid_cleanup_reconciliation_proof.for_session(
            revoked_identity_session,
        ),
        operation_id=operation_id,
    )
    SessionReconciliationRepository.create(
        session_id=revoked_identity_session.id,
        proof=refresh_proof,
    )
    SessionReconciliationRepository.create(
        session_id=revoked_identity_session.id,
        proof=cleanup_proof,
    )
    assert set(
        SessionReconciliationRecord.objects.filter(
            session=revoked_identity_session,
            operation_id=operation_id,
        ).values_list("kind", flat=True)
    ) == {
        SessionReconciliationRecord.Kind.REFRESH_ROTATION,
        SessionReconciliationRecord.Kind.SESSION_FAMILY_CLEANUP,
    }


@pytest.mark.django_db(transaction=True)
def test_malformed_persisted_proof_stays_unresolved_and_fail_closed(
    revoked_identity_session,
    redis,
):
    record = SessionReconciliationRecord.objects.create(
        session=revoked_identity_session,
        kind=SessionReconciliationRecord.Kind.REFRESH_ROTATION,
        operation_id=uuid.uuid4(),
        expected_db_state={"schema_version": 999, "unexpected": "field"},
        expected_redis_state={},
        failure_reason=ReconciliationFailureCode.REDIS_CAS_FAILED.value,
    )
    redis.set(refresh_family_key(revoked_identity_session), "must-not-authorize")

    result = reconcile_identity_sessions(batch_size=10, redis_client=redis)

    record.refresh_from_db()
    revoked_identity_session.refresh_from_db()
    assert result.invalid == 1
    assert record.resolved_at is None
    assert revoked_identity_session.revoked_at is not None
    assert not redis.exists(refresh_family_key(revoked_identity_session))
    assert record.failure_reason == ReconciliationPayloadErrorCode.INVALID.value
```

- [ ] **Step 3: Run and verify failure**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_tenant_grant_switch_repository.py apps/users/tests/test_tenant_switch_service.py apps/users/tests/test_session_reconciliation.py apps/users/tests/test_identity_merge_locking.py apps/users/tests/test_deferred_merge_entrypoint_coverage.py -v
```

- [ ] **Step 4: Persist switch intent, then execute the narrow DB transition**

Authentication of an initial manual selection has two branches. The normal
branch validates signature, `kid`, `iss`, `aud`, `type=TENANT_SELECTION`,
`jti`, identity/challenge claims, `iat` and the five-minute `exp`. Only if that
decode fails specifically because of `exp` may the replay branch decode again
with expiry verification disabled; every other validation remains mandatory.
The replay branch then requires all of the following before it touches any
result envelope:

1. the supplied `operation_id` already identifies one operation;
2. its status is `DB_COMMITTED` or `COMPLETED`, with result session/grant IDs;
3. `operation.created_at <= selection_claims.exp`;
4. target tenant and every identity/current-context claim exactly match;
5. `HMAC-SHA-256(AUTH_TOKEN_HMAC_PEPPER, raw_selection_token)` is an input to
   the persisted request fingerprint, and the recomputed fingerprint matches;
6. the response envelope remains inside its ten-minute TTL;
7. the existing session passes the replay validity checks in Step 5.

An unknown operation, `PENDING`/`FAILED` operation, changed target/fingerprint
or absent result returns `TENANT_SELECTION_EXPIRED`. The expired-token branch
cannot insert an operation, create/replace a session, create a grant or mint
tokens. If a matching `PENDING` intent already created a no-token session
before a crash, a separate fail-closed transaction marks the operation failed
and revokes that existing session; it never resumes the grant transition.
There is no global "ignore exp" middleware mode.

First use a short global `transaction.atomic()` through
`IdentityMergeLockCoordinator`. Read-only discovery starts from signed
canonical Identity/current-context claims plus `operation_id`, then takes the
global advisory/User/merge locks, then locks every signed current/target
`IamTenantRegistryEntry` in UUID order before any challenge, session or switch
row. A missing registry row returns typed `TENANT_CONTEXT_INVALID`; request
input never auto-registers a tenant. It then locks `PreAuthChallenge`, an
existing `IdentitySession`, and an existing `TenantGrantSwitchOperation` in
the closed rank. For initial tenant selection it creates the session before
the FK-dependent operation, using
`security_version_snapshot=locked_identity.security_version`, then inserts the
operation as `PENDING` and consumes the MFA challenge. No access/refresh token
or grant exists yet. A transaction-scoped operation-ID advisory key serializes
the no-row-yet idempotency case without preceding the sorted Identity locks.

Concurrent inserts of the same `operation_id` use that advisory key plus an
inner savepoint: one insert wins, the other catches any residual unique
violation outside the savepoint, restarts full coordinator discovery and
returns the identical locked result. It never locks the newly observed
operation incrementally. Two different operations for one session serialize
on the `IdentitySession` row. After the winner commits, the loser detects the
signed old context is stale, records a terminal `TENANT_SWITCHED` failure and
creates no second grant.

Add `test_unregistered_target_tenant_fails_before_switch_intent`: a validly
signed but uninventoried target returns `TENANT_CONTEXT_INVALID`, creates no
registry row, session, switch operation, grant, token or envelope, and performs
no RLS tenant query. Registered fixtures prove the registry locks precede
`PreAuthChallenge`/session locks in a captured two-tenant SQL trace.

Then use a second `transaction.atomic(using="default")` for the grant
transition, again through complete coordinator discovery/locking; the first
transaction is not reusable lock evidence. Replica access is forbidden. Global-table ORM operations use
`.using("default")`; all RLS-dependent grant and membership
lock/update/insert operations use one parameterized cursor from
`connections["default"]`. No tenant-scoped ORM operation may pass through
`EnterpriseClusterRouter`.

The private tenant boundary restores the exact request token:

```python
@contextmanager
def _rls_tenant(cursor, tenant_id: uuid.UUID):
    token = ContextCore.tenant_id.set(tenant_id)
    try:
        cursor.execute(
            "SET LOCAL app.current_tenant_id = %s",
            [str(tenant_id)],
        )
        yield
    finally:
        ContextCore.tenant_id.reset(token)
```

This helper is private to `tenant_grant_switch_repository.py`; it neither
disables RLS nor exposes cross-tenant ORM. Within one default cursor:

1. after sorted User/merge locks, lock all current/target registry rows in UUID
   order, then the global `IdentitySession` and
   `TenantGrantSwitchOperation`; reject any absent registry row or graph hold
   before transition;
2. reject a replay whose request fingerprint differs;
3. materialize current and target tenant intents, sort tenant UUID bytes, and
   enter each tenant exactly once. In each tenant lock
   `TenantAuthorizationState`, the exact membership, relevant `RoleBinding`
   rows and only then the exact current `TenantSessionGrant`, all by PK. A
   held matching row returns typed 403 rather than appearing absent;
4. re-read the complete graph, signed session/context/open state, membership/
   session Identity, tenant invariants, versions and holds. A new participant
   returns `MERGE_GRAPH_CHANGED_RETRY` and restarts the whole transaction;
5. CAS-supersede the current open grant even if expired;
6. insert one target grant only after target membership and policy state are
   locked and validated;
7. call the injected pure token-response builder with the locked
   Identity/session/membership/grant/policy versions, generate one refresh
   verifier and CSRF value, and persist their AES-256-GCM envelope plus the
   refresh digest, expected/result Redis generation/digests and result
   IDs/version with `status=DB_COMMITTED` in this same transaction. Initial
   selection persists result generation `0`; a later switch CASes `N -> N+1`;
8. commit.

`SwitchResponseKeyring` uses a dedicated Vault-backed current/previous keyring,
associated data
`b"complect:tenant-switch:v1" || operation_id.bytes ||
bytes.fromhex(request_fingerprint)`, a fresh
96-bit nonce and `response_expires_at=created_at+10 minutes`. The envelope JSON
contains exactly access, refresh and CSRF values plus their cookie expiry; no
email, password or MFA material. Logs, audit and outbox contain only operation
ID/status. The response builder is injected into the narrow repository as a
pure callback; it cannot query ORM or Redis. This closes the crash window
between grant commit and recoverable token material.

The repository never queries an RLS-protected grant by only `session_id`,
never uses `replica`, and never calls tenant-scoped ORM between its `SET LOCAL`
operations. The A-context/B-target test monkeypatches router injection to fail,
proving the switch path does not allow the router to restore A.

- [ ] **Step 5: Implement post-commit refresh rotation and fail-closed recovery**

`tenant_switch_service` prepares the durable intent, calls the DB transition,
then decrypts the stored result only in process memory and performs the Task 7
Redis Lua state machine keyed by `operation_id`. Initial auto-select, initial
manual select and recovery of their `DB_COMMITTED` process-crash window use
`INITIALIZE`; later switch uses `ROTATE`. Redis stores the last completed
switch operation and digest; repeating that same operation/digest returns
`REPLAYED` without incrementing the refresh generation. `REUSED` revokes the
session family; `MISSING_UNEXPECTED` creates fail-closed reconciliation. Catch DB exceptions outside
`transaction.atomic()`. On either DB rollback or Redis failure call:

```python
@transaction.atomic
def record_failed_switch_and_revoke_session(
    failure: SwitchFailure,
) -> FailClosedSwitchResult:
    graph = IdentityMergeLockCoordinator.lock_switch_failure_graph(
        session_id=failure.session_id,
        operation_id=failure.operation_id,
    )
    session = graph.session
    operation = graph.switch_operation
    locked = TenantGrantSwitchOperationRepository.classify_locked_failure(
        operation=operation,
        session=session,
        failure=failure,
    )
    if locked is LockedFailureState.SAME_OPERATION_COMPLETED:
        return FailClosedSwitchResult.ALREADY_COMPLETED

    assert_no_legal_hold_for_switch_failure(
        session=session,
        operation=operation,
        locked_result_grant=locked.result_grant,
        locked_membership=locked.membership,
    )
    IdentitySessionRepository.revoke(
        session_id=session.id,
        expected_version=session.version,
        reason="tenant_switch_reconciliation",
    )
    TenantGrantSwitchOperationRepository.cas_terminal_failure(
        operation_id=operation.operation_id,
        expected_version=operation.version,
        failure=failure,
    )
    SessionReconciliationRepository.create(
        session_id=failure.session_id,
        proof=failure.reconciliation_proof,
    )
    return FailClosedSwitchResult.REVOKED
```

There is no `revoke_without_expected_version()` escape hatch. The handler
discovers canonical/merge/session/operation/result membership/grant first,
takes the full global order, reclassifies the failure under those locks,
performs the complete legal-hold precheck, and then CASes each mutation with
the version it just read. It never locks session/operation and then reaches
backward for User or merge rows. If the same operation completed successfully
in a concurrent request, the handler returns `ALREADY_COMPLETED` and creates
no revoke or reconciliation row. A different or still-ambiguous committed
transition remains fail-closed.

After Redis CAS succeeds, CAS `DB_COMMITTED -> COMPLETED` and return the exact
envelope response. Replay rules are closed:

- before decrypting either `DB_COMMITTED` or `COMPLETED`, read from primary and
  prove `session.revoked_at IS NULL`, `session.expires_at > now`,
  `session.security_version_snapshot == Identity.security_version`, then enter
  `operation.result_tenant_id` RLS context and prove the exact result grant
  belongs to that session/membership/Identity, is open, unexpired and has the
  recorded context version. A non-held validation failure cryptoshreds the
  envelope and returns `SESSION_REVOKED`,
  `IDENTITY_SECURITY_VERSION_STALE` or `TENANT_SWITCHED`; a held row is never
  decrypted or replayed, returns typed 403 and remains byte-for-byte unchanged;
- same `operation_id` plus same fingerprint in `COMPLETED`: after those checks,
  decrypt and return the identical response while the ten-minute envelope exists;
- same operation in `DB_COMMITTED`: complete the idempotent Redis CAS, mark
  complete and return that same response;
- same operation with a different fingerprint: `IDEMPOTENCY_KEY_REUSED`;
- expired/missing envelope on `COMPLETED`: `IDEMPOTENCY_RESULT_EXPIRED`, with
  no new rotation and no change to the already-issued session;
- expired/missing/corrupt envelope before Redis completion: for non-held rows,
  revoke the session and create reconciliation state; for a held target,
  create no mutation and escalate the blocked operation to the legal-hold
  release workflow.

`switch_response_retention` runs every minute on the `retention` queue and
CAS-clears ciphertext/nonce/key ID after `response_expires_at`. It never
decrypts or replays expired material. A legal hold blocks that row mutation,
so the encrypted envelope and its referenced key remain preserved but
ineligible for replay until the separately authorized hold release permits
cryptoshredding. Vault key-retention policy must not destroy a key still
referenced by a held envelope.

Task 5/7 session revoke primitives also null ciphertext, nonce and key ID for
every live non-held operation bound to the revoked session. They first lock
and reject any held target atomically. Thus successful logout, logout-all,
password/MFA recovery and refresh-reuse response cannot leave a replayable
credential envelope behind; a hold-denied attempt changes nothing and the
envelope remains non-replayable by expiry/validation policy.

- [ ] **Step 6: Implement the fail-closed reconciliation worker**

`reconcile_identity_sessions` reads unresolved candidate IDs from primary
without a target row lock. For each candidate it discovers the canonical
Identity/merge/session/switch/tenant graph, takes the standard locks, then
claims the `SessionReconciliationRecord` at its global rank with a
non-blocking transaction advisory work key. A busy candidate is counted and
rescheduled; it is never `select_for_update(skip_locked=True)` before User.
Under those locks it compares expected/current DB state for diagnostics and
branches only on the closed `kind`:

1. `TENANT_SWITCH`: require and lock the exact FK-linked
   `switch_operation`; CAS-revoke an open session, change a linked
   `PENDING`/`DB_COMMITTED` operation to
   `FAILED(failure_code="RECONCILED_FAIL_CLOSED")`, then delete the Redis
   family.
2. `REFRESH_ROTATION`: require `switch_operation_id IS NULL`, CAS-revoke an
   open session and delete the Redis family. It performs no switch-operation
   lookup.
3. `SESSION_FAMILY_CLEANUP`: require `switch_operation_id IS NULL` and the
   expected DB state to prove the session is already revoked, then only ensure
   the complete Redis family is absent. It never rewrites session authority
   and performs no switch-operation lookup.
4. In every branch, CAS-set `resolved_at` only after its DB fail-closed
   postcondition and Redis deletion are proven.

`expected_db_state`/`expected_redis_state` contain only UUIDs, generations,
digests and closed enum states; raw access/refresh/CSRF material is forbidden.
The worker deserializes and validates the exact per-kind schema before making
any branch decision. For an unknown kind, schema version, missing/extra key or
invalid value it records `RECONCILIATION_PAYLOAD_INVALID`, keeps
`resolved_at=NULL`, ensures the session remains/reaches revoked state and
best-effort deletes the Redis family; it never reconstructs authority from a
malformed payload. One invalid row is isolated and reported as
`ReconciliationBatchResult.invalid` rather than aborting the rest of the
batch. If the reconciliation/session/linked operation is held, even those
fail-closed mutations are forbidden: the row remains unresolved, DB and Redis
stay unchanged, and the batch reports `legal_hold_blocked` for operator
escalation. Task 7 refresh/logout tests and the Task 8 switch failure tests assert
the exact serialized key set, schema version, kind, operation/FK binding and
source-specific DB state produced by each real call site.

Add two-connection barrier tests for merge/finalize versus tenant switch,
switch failure handling and reconciliation, plus a hold latch that commits
after discovery but before lock materialization. Accepted results are one
serialized success or typed `MERGE_GRAPH_CHANGED_RETRY`/hold denial. SQLSTATE
`40P01`, a grant created under a winning hold, or any token/envelope returned
from an incompletely locked graph fails the suite.

`SessionReconciliationRepository` is the only serializer/persistence boundary
for the three proof dataclasses. It validates the closed schema, enforces the
tuple/fingerprint idempotency contract and performs every create/resolve CAS;
the service never writes reconciliation JSON with model `.create()` outside
explicit corrupt-row tests.

The Celery task derives from `BaseEnterpriseTask`, runs on `retention`, retries
Redis/transient DB failures with delays `60, 120, 240, 480, 960` seconds and
increments `attempt_count` once per attempt. After five failed retries the
existing `BaseEnterpriseTask.on_failure()` writes the non-secret task payload
(`reconciliation_record_id` only) to the infrastructure DLQ; the durable row
remains unresolved for operator replay. A management command invokes the same
batch service as `reconcile_identity_sessions --batch-size <n>` and exits
non-zero when any row remains invalid, legal-hold-blocked or exhausted. The
`(session_id, kind, operation_id)` unique constraint plus
repository `get_or_create` makes each source idempotent without conflating
refresh, switch and logout cleanup operations.

- [ ] **Step 7: Wire single-membership auto-select and multi-membership selection**

After `verify_mfa()`:

- one enabled membership: `complete_tenant_selection()` immediately creates session/grant and tokens;
- several: API returns `TENANT_SELECTION`; no session exists until target selection;
- none: return `NO_ENABLED_MEMBERSHIP`;
- first selection passes null expected current grant/tenant/context; its session
  is created only in the committed switch-intent transaction after target
  selection, then revoked if the grant transition fails. Both auto-select and
  manual-select create a durable operation with initial DB/Redis generation
  `0`; absence of the Redis family is valid only for that proved
  `DB_COMMITTED` initial operation.

Refresh context restoration or re-selection calls the same repository with
the signed old grant/tenant/context. It must supersede an expired open grant
before inserting a replacement; no login/refresh service may insert grants
directly.

- [ ] **Step 8: Run the complete switch/reconciliation suite**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_tenant_grant_switch_repository.py apps/users/tests/test_tenant_switch_service.py apps/users/tests/test_session_reconciliation.py apps/users/tests/test_identity_session_service.py apps/users/tests/test_preauth_service.py apps/users/tests/test_identity_merge_locking.py apps/users/tests/test_deferred_merge_entrypoint_coverage.py -v
```

Expected: PASS for the switch matrix, both real concurrency races and both
failure boundaries.

- [ ] **Step 9: Commit**

```bash
git add backend/apps/users/repositories/tenant_grant_switch_repository.py backend/apps/users/repositories/session_reconciliation_repository.py backend/apps/users/services/tenant_switch_service.py backend/apps/users/services/switch_response_keyring.py backend/apps/users/services/auth_token_codec.py backend/apps/users/services/refresh_rotation_service.py backend/apps/users/services/session_reconciliation_service.py backend/apps/users/security/identity_merge_locking.py backend/apps/users/security/credential_entrypoints.py backend/apps/users/security/credential_entrypoint_manifest.json backend/apps/users/tasks/session_reconciliation.py backend/apps/users/tasks/switch_response_retention.py backend/apps/users/management/commands/reconcile_identity_sessions.py backend/config/settings/base.py backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py backend/apps/users/tests/test_tenant_grant_switch_repository.py backend/apps/users/tests/test_tenant_switch_service.py backend/apps/users/tests/test_session_reconciliation.py backend/apps/users/tests/test_identity_merge_locking.py backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py
git commit -m "feat(iam): add rls safe tenant switching"
```

---

### Task 9: MFA Credentials, Backup Codes and Password Recovery

**Files:**

- Modify: `backend/apps/users/services/mfa_service.py`
- Create: `backend/apps/users/services/auth_keyring.py`
- Create: `backend/apps/users/services/recovery_service.py`
- Create: `backend/apps/users/services/credential_service.py`
- Modify: `backend/apps/users/services/user_admin_service.py`
- Modify: `backend/apps/users/services/deferred_merge_guard.py`
- Modify: `backend/apps/users/services/deferred_merge_credential_synchronizer.py`
- Modify: `backend/apps/users/security/identity_merge_locking.py`
- Modify: `backend/apps/users/services/pii_access_audit_service.py`
- Create: `backend/apps/users/events.py`
- Create: `backend/apps/users/tasks/recovery_delivery.py`
- Create: `backend/apps/users/tasks/mfa_credential_retention.py`
- Create: `backend/apps/users/security/password_policy.py`
- Modify: `backend/apps/common/services/outbox_service.py`
- Modify: `backend/apps/common/tasks/outbox_dispatcher.py`
- Modify: `backend/config/settings/base.py`
- Modify: `backend/apps/users/tasks/identity_backfill.py`
- Modify: `backend/apps/users/security/credential_entrypoints.py`
- Modify: `backend/apps/users/security/credential_entrypoint_manifest.json`
- Test: `backend/apps/users/tests/test_identity_mfa_recovery.py`
- Test: `backend/apps/users/tests/test_deferred_merge_convergence.py`
- Test: `backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py`
- Test: `backend/apps/users/tests/test_identity_merge_locking.py`

**Interfaces:**

- Produces `DatabaseMfaFactorVerifier` for Task 6.
- Produces Task 6 `MfaEnrollment`: `setup_totp(identity_id, challenge_id) ->
  MfaSetup` and `confirm_totp(identity_id, challenge_id, code) ->
  MfaEnrollmentResult`.
- Produces `DatabaseMfaEnrollment.begin()`/`.confirm()` as the concrete Task 6
  adapter over those challenge-bound functions.
- `MfaEnrollmentResult` contains the new `security_version` and exactly ten
  plaintext backup codes returned once.
- Produces
  `start_recovery(email, purpose: RecoveryToken.Purpose) -> RecoveryAccepted`;
  the result never contains selector/verifier material.
- Produces `complete_password_recovery(selector, verifier, new_password)` and
  `complete_mfa_recovery(selector, verifier)`.
- Produces `change_password(identity_id, current_password, new_password) ->
  None` for an owner-authenticated normal password change.
- Produces `deliver_recovery_operation(operation_id: str)` and a retention sweep for
  pending/expired encrypted delivery envelopes.
- Produces a sensitive `RecoveryDeliveryTask` with allowlisted DLQ/log fields;
  it never delegates a decrypted provider failure to generic raw failure
  capture.
- Extends the Task 2 general `PiiAccessAuditService`; recovery uses the same
  audit-before-PII contract and does not create a recovery-only audit path.
- Produces append-only `pii.identity_email_read` audit records before each
  recovery worker decryption, with system actor, closed reason and the
  operation's request/correlation/causal IDs; raw email is never audited.
- Produces `validate_new_password(identity, password) -> None` for the exact
  12–128/Django-validator policy.
- Registers password change/admin reset, password/MFA recovery, MFA
  confirm/reset and global invalidation call sites in the closed merge-guard
  manifest. Each path resolves and locks the complete realized merge graph
  before authority decisions; this task's artifact cannot reuse Task 2's
  attestation.
- Routes every password hash, MFA, global status and recovery-completion
  mutation through `DeferredMergeCredentialSynchronizer` under the common
  graph coordinator. The canonical row is the sole authority; source rows
  receive only the exact closed matrix effect, and one held graph participant
  makes the whole operation byte-for-byte unchanged.
- Applies `require_activation_allowed()` before MFA confirmation, global
  unlock or any other transition to `ACTIVE`; dormant deferred/ready graphs
  return `MERGE_DORMANT_FINALIZATION_REQUIRED` until Task 16 finalizes them.
- Stores encryption/signing `key_id`; stores only recovery/backup verifier hashes.

- [ ] **Step 1: Write failing credential/recovery tests**

```python
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.utils import timezone

from django.core import mail

from apps.common.models.outbox import OutboxRecord
from apps.common.services.dlq_service import inspect_dead_letters
from apps.common.tasks.outbox_dispatcher import run_outbox_polling_dispatcher_task
from apps.users.models import (
    BackupCode,
    IdentitySecurityAuditRecord,
    IdentitySession,
    MfaCredential,
    PreAuthChallenge,
    RecoveryOperation,
    RecoveryToken,
    User,
)
from apps.users.security.password_policy import validate_new_password
from apps.users.services.auth_errors import AuthFlowError
from apps.users.services.credential_service import change_password
from apps.users.services.mfa_service import (
    DatabaseMfaFactorVerifier,
    confirm_totp,
    setup_totp,
)
from apps.users.services.preauth_service import LoginIdentifier, verify_password
from apps.users.services.pii_access_audit_service import PiiAuditUnavailable
from apps.users.services.recovery_service import (
    complete_mfa_recovery,
    complete_password_recovery,
    decode_recovery_selector,
    start_recovery,
)
from apps.users.tasks.recovery_delivery import (
    deliver_recovery_operation,
    deliver_recovery_operation_task,
)
from apps.users.tests.iam_factories import decode_stream_event


@pytest.fixture(autouse=True)
def auth_crypto_keys(settings):
    settings.MFA_ACTIVE_KEY_ID = "test-mfa-v1"
    settings.MFA_ENCRYPTION_KEYRING = {
        "test-mfa-v1": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    }
    settings.AUTH_TOKEN_HMAC_PEPPER = "test-only-token-pepper"
    settings.RECOVERY_DELIVERY_ACTIVE_KEY_ID = "test-delivery-v1"
    settings.RECOVERY_DELIVERY_KEYRING = {
        "test-delivery-v1": "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
    }
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


@pytest.fixture(autouse=True)
def recovery_request_context(request_trace_context):
    with request_trace_context(
        request_id=uuid.UUID("00000000-0000-4000-8000-000000000091"),
        correlation_id=uuid.UUID("00000000-0000-4000-8000-000000000092"),
        causal_root_id=uuid.UUID("00000000-0000-4000-8000-000000000093"),
    ):
        yield


def parse_selector_verifier_from_mail(message):
    recovery_url = next(
        part for part in message.body.split() if part.startswith("https://")
    )
    query = parse_qs(urlparse(recovery_url).fragment)
    return query["selector"][0], query["verifier"][0]


@pytest.mark.django_db(transaction=True)
def test_totp_secret_is_encrypted_with_key_id(identity, mfa_setup_challenge):
    challenge_id = mfa_setup_challenge.id
    setup = setup_totp(identity.identity_id, challenge_id)
    credential = MfaCredential.objects.get(identity_id=identity.identity_id)
    assert credential.key_id
    assert credential.setup_challenge_id == challenge_id
    assert credential.encrypted_secret != setup.secret.encode()
    assert credential.confirmed_at is None


@pytest.mark.django_db(transaction=True)
def test_confirm_totp_increments_security_version(identity, mfa_setup_challenge):
    challenge_id = mfa_setup_challenge.id
    User.objects.filter(pk=identity.pk).update(
        security_status=User.SecurityStatus.MFA_SETUP_REQUIRED,
        security_version=7,
    )
    PreAuthChallenge.objects.filter(pk=challenge_id).update(
        security_version_snapshot=7,
    )
    setup = setup_totp(identity.identity_id, challenge_id)
    result = confirm_totp(
        identity.identity_id,
        challenge_id,
        pyotp.TOTP(setup.secret).now(),
    )
    identity.refresh_from_db()
    assert identity.security_version == 8
    assert identity.security_status == User.SecurityStatus.ACTIVE
    assert len(result.backup_codes) == 10
    assert len(set(result.backup_codes)) == 10


@pytest.mark.django_db(transaction=True)
def test_abandoned_setup_can_start_fresh_after_challenge_expiry(
    identity,
    mfa_setup_challenge,
):
    abandoned = setup_totp(identity.identity_id, mfa_setup_challenge.id)
    PreAuthChallenge.objects.filter(pk=mfa_setup_challenge.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    password_result = verify_password(
        LoginIdentifier(kind="email", value=identity.normalized_email),
        "Pass1!",
        {"browser": "fresh"},
    )
    fresh = setup_totp(identity.identity_id, password_result.challenge_id)
    result = confirm_totp(
        identity.identity_id,
        password_result.challenge_id,
        pyotp.TOTP(fresh.secret).now(),
    )

    assert fresh.secret != abandoned.secret
    assert result.security_version == 2
    assert not MfaCredential.objects.filter(
        identity_id=identity.identity_id,
        revoked_at__isnull=True,
        confirmed_at__isnull=True,
    ).exists()
    assert MfaCredential.objects.filter(
        identity_id=identity.identity_id,
        revoked_at__isnull=True,
        confirmed_at__isnull=False,
    ).count() == 1


@pytest.mark.django_db(transaction=True)
def test_backup_code_is_hash_only_and_one_time(identity_with_backup_codes):
    identity, codes = identity_with_backup_codes
    assert codes[0] not in BackupCode.objects.values_list("code_hash", flat=True)
    DatabaseMfaFactorVerifier().verify(identity.identity_id, codes[0])
    with pytest.raises(AuthFlowError, match="BACKUP_CODE_USED"):
        DatabaseMfaFactorVerifier().verify(identity.identity_id, codes[0])


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_uses_of_one_backup_code_have_one_success(
    identity_with_backup_codes,
):
    identity, codes = identity_with_backup_codes
    barrier = Barrier(2)

    def consume():
        close_old_connections()
        try:
            barrier.wait()
            try:
                DatabaseMfaFactorVerifier().verify(identity.identity_id, codes[0])
                return "success"
            except AuthFlowError as exc:
                return exc.code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume(), range(2)))

    assert results.count("success") == 1
    assert results.count("BACKUP_CODE_USED") == 1


@pytest.mark.django_db(transaction=True)
def test_recovery_start_returns_no_secret_and_outbox_payload_is_safe(identity):
    accepted = start_recovery(
        identity.normalized_email,
        RecoveryToken.Purpose.PASSWORD_RESET,
    )

    assert accepted.status == "accepted"
    assert not hasattr(accepted, "selector")
    assert not hasattr(accepted, "verifier")
    token = RecoveryToken.objects.get(
        identity_id=identity.identity_id,
        purpose=RecoveryToken.Purpose.PASSWORD_RESET,
        revoked_at__isnull=True,
    )
    operation = token.operation
    command = OutboxRecord.objects.get(
        event_type="auth.recovery_delivery_requested",
    )
    serialized = json.dumps(command.payload_json)
    assert command.payload_json["operation_id"] == str(operation.id)
    assert identity.normalized_email not in serialized
    assert "verifier" not in serialized
    assert "selector" not in serialized


@pytest.mark.django_db(transaction=True)
def test_held_identity_recovery_start_is_accepted_without_mutation(identity):
    User.objects.filter(pk=identity.pk).update(identity_legal_hold=True)

    accepted = start_recovery(
        identity.normalized_email,
        RecoveryToken.Purpose.PASSWORD_RESET,
    )

    assert accepted.status == "accepted"
    assert not RecoveryToken.objects.filter(
        identity_id=identity.identity_id,
    ).exists()
    assert not RecoveryOperation.objects.exists()
    assert not OutboxRecord.objects.filter(
        event_type="auth.recovery_delivery_requested",
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_global_recovery_command_routes_to_global_stream(identity, redis):
    stream = "erp:streams:tenant:global"
    redis.delete(stream)
    start_recovery(
        identity.normalized_email,
        RecoveryToken.Purpose.PASSWORD_RESET,
    )
    operation = RecoveryOperation.objects.get(
        token__identity_id=identity.identity_id,
        token__purpose=RecoveryToken.Purpose.PASSWORD_RESET,
    )
    run_outbox_polling_dispatcher_task()
    events = [
        decode_stream_event(message)
        for _, message in redis.xrange(stream)
    ]
    matching = [
        event
        for event in events
        if event["event_type"] == "auth.recovery_delivery_requested"
        and event["payload"]["operation_id"] == str(operation.id)
    ]
    assert len(matching) == 1
    assert matching[0]["payload"]["tenant_id"] is None


def delivered_recovery_material(identity, purpose):
    start_recovery(identity.normalized_email, purpose)
    token = RecoveryToken.objects.get(
        identity_id=identity.identity_id,
        purpose=purpose,
        revoked_at__isnull=True,
    )
    deliver_recovery_operation(str(token.operation_id))
    return parse_selector_verifier_from_mail(mail.outbox[-1])


@pytest.mark.django_db(transaction=True)
def test_password_recovery_changes_password_and_revokes_sessions(
    identity,
    identity_session,
):
    User.objects.filter(pk=identity.pk).update(security_version=7)
    IdentitySession.objects.filter(pk=identity_session.pk).update(
        security_version_snapshot=7,
    )
    selector, verifier = delivered_recovery_material(
        identity,
        RecoveryToken.Purpose.PASSWORD_RESET,
    )
    complete_password_recovery(
        selector,
        verifier,
        "New-Strong-Password-1!",
    )
    identity.refresh_from_db()
    identity_session.refresh_from_db()
    assert identity.check_password("New-Strong-Password-1!")
    assert identity.security_version == 8
    assert identity_session.revoked_at is not None


@pytest.mark.django_db(transaction=True)
def test_owner_password_change_checks_current_password_and_revokes_sessions(
    identity,
    identity_session,
):
    User.objects.filter(pk=identity.pk).update(security_version=7)
    IdentitySession.objects.filter(pk=identity_session.pk).update(
        security_version_snapshot=7,
    )
    change_password(
        identity.identity_id,
        current_password="Pass1!",
        new_password="New-Strong-Password-1!",
    )
    identity.refresh_from_db()
    identity_session.refresh_from_db()
    assert identity.check_password("New-Strong-Password-1!")
    assert identity.security_version == 8
    assert identity_session.revoked_at is not None


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_recovery_consumes_have_one_success(identity):
    selector, verifier = delivered_recovery_material(
        identity,
        RecoveryToken.Purpose.PASSWORD_RESET,
    )
    barrier = Barrier(2)

    def consume():
        close_old_connections()
        try:
            barrier.wait()
            try:
                complete_password_recovery(
                    selector,
                    verifier,
                    "New-Strong-Password-1!",
                )
                return "success"
            except AuthFlowError as exc:
                return exc.code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume(), range(2)))

    assert results.count("success") == 1
    assert results.count("RECOVERY_TOKEN_USED") == 1


@pytest.mark.django_db(transaction=True)
def test_mfa_recovery_requires_owner_delivery_and_forces_fresh_setup(
    identity_with_totp_and_backup_codes,
    identity_session,
):
    identity = identity_with_totp_and_backup_codes.identity
    User.objects.filter(pk=identity.pk).update(security_version=7)
    IdentitySession.objects.filter(pk=identity_session.pk).update(
        security_version_snapshot=7,
    )
    selector, verifier = delivered_recovery_material(
        identity,
        RecoveryToken.Purpose.MFA_RESET,
    )

    complete_mfa_recovery(selector, verifier)

    identity.refresh_from_db()
    identity_session.refresh_from_db()
    assert identity.security_status == User.SecurityStatus.MFA_SETUP_REQUIRED
    assert identity.security_version == 8
    assert identity_session.revoked_at is not None
    assert not MfaCredential.objects.filter(
        identity_id=identity.identity_id,
        revoked_at__isnull=True,
    ).exists()
    assert not BackupCode.objects.filter(
        identity_id=identity.identity_id,
        revoked_at__isnull=True,
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_new_recovery_revokes_previous_open_token_for_same_purpose(identity):
    start_recovery(
        identity.normalized_email,
        RecoveryToken.Purpose.PASSWORD_RESET,
    )
    first = RecoveryToken.objects.get(
        identity_id=identity.identity_id,
        purpose=RecoveryToken.Purpose.PASSWORD_RESET,
        revoked_at__isnull=True,
    )

    start_recovery(
        identity.normalized_email,
        RecoveryToken.Purpose.PASSWORD_RESET,
    )

    first.refresh_from_db()
    assert first.revoked_at is not None
    assert RecoveryToken.objects.filter(
        identity_id=identity.identity_id,
        purpose=RecoveryToken.Purpose.PASSWORD_RESET,
        consumed_at__isnull=True,
        revoked_at__isnull=True,
    ).count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_sequential_recoveries_bind_delivery_and_completion_exactly(
    identity,
):
    start_recovery(
        identity.normalized_email,
        RecoveryToken.Purpose.PASSWORD_RESET,
    )
    first_token = RecoveryToken.objects.get(
        identity_id=identity.identity_id,
        purpose=RecoveryToken.Purpose.PASSWORD_RESET,
        revoked_at__isnull=True,
    )
    first_operation = first_token.operation
    deliver_recovery_operation(str(first_operation.id))

    start_recovery(
        identity.normalized_email,
        RecoveryToken.Purpose.PASSWORD_RESET,
    )
    second_token = RecoveryToken.objects.get(
        identity_id=identity.identity_id,
        purpose=RecoveryToken.Purpose.PASSWORD_RESET,
        revoked_at__isnull=True,
    )
    second_operation = second_token.operation

    first_token.refresh_from_db()
    first_operation.refresh_from_db()
    assert first_token.revoked_at is not None
    assert first_operation.status == RecoveryOperation.Status.SUPERSEDED
    assert second_operation.id != first_operation.id

    deliver_recovery_operation(str(second_operation.id))
    selector, verifier = parse_selector_verifier_from_mail(mail.outbox[-1])
    assert decode_recovery_selector(selector)["recovery_id"] == str(second_token.id)
    complete_password_recovery(selector, verifier, "New-Strong-Password-1!")

    first_operation.refresh_from_db()
    second_operation.refresh_from_db()
    assert first_operation.status == RecoveryOperation.Status.SUPERSEDED
    assert first_operation.completed_at is None
    assert second_operation.status == RecoveryOperation.Status.COMPLETED
    assert second_operation.completed_at is not None


@pytest.mark.django_db(transaction=True)
def test_two_recovery_delivery_workers_send_once(identity):
    start_recovery(
        identity.normalized_email,
        RecoveryToken.Purpose.PASSWORD_RESET,
    )
    operation = RecoveryToken.objects.get(
        identity_id=identity.identity_id,
        purpose=RecoveryToken.Purpose.PASSWORD_RESET,
        revoked_at__isnull=True,
    ).operation
    barrier = Barrier(2)

    def deliver():
        close_old_connections()
        try:
            barrier.wait()
            return deliver_recovery_operation(str(operation.id))
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: deliver(), range(2)))

    operation.refresh_from_db()
    assert operation.status == RecoveryOperation.Status.DELIVERED
    assert len(mail.outbox) == 1
    assert [result.status for result in results].count("delivered") == 1
    assert [result.status for result in results].count("already_claimed") == 1
    audit = IdentitySecurityAuditRecord.objects.get(
        event_type="pii.identity_email_read",
        metadata__operation_id=str(operation.id),
    )
    assert audit.actor_type == "system"
    assert audit.actor_ref == "recovery-delivery-worker"
    assert audit.reason_code == "RECOVERY_OWNER_DELIVERY"
    assert audit.request_id == operation.request_id
    assert audit.correlation_id == operation.correlation_id
    assert set(audit.metadata) == {"operation_id", "delivery_attempt_id"}
    assert audit.metadata["operation_id"] == str(operation.id)
    uuid.UUID(audit.metadata["delivery_attempt_id"])
    assert identity.email not in json.dumps(audit.metadata)


@pytest.mark.django_db(transaction=True)
def test_recovery_audit_failure_rolls_back_claim_before_decryption(
    identity,
    failing_pii_audit,
    recovery_decrypt_spy,
):
    start_recovery(
        identity.normalized_email,
        RecoveryToken.Purpose.PASSWORD_RESET,
    )
    operation = RecoveryToken.objects.get(
        identity_id=identity.identity_id,
    ).operation

    with pytest.raises(PiiAuditUnavailable):
        deliver_recovery_operation(
            str(operation.id),
            pii_audit=failing_pii_audit,
            decryptor=recovery_decrypt_spy,
        )

    operation.refresh_from_db()
    assert operation.status == RecoveryOperation.Status.PENDING_DELIVERY
    assert operation.delivery_attempt_id is None
    assert operation.delivery_lease_expires_at is None
    assert operation.attempt_count == 0
    recovery_decrypt_spy.assert_not_called()
    assert mail.outbox == []


@pytest.mark.django_db(transaction=True)
def test_recovery_provider_failure_uses_redacted_dlq(
    identity,
    redis,
    leaking_recovery_provider,
    caplog,
):
    start_recovery(
        identity.normalized_email,
        RecoveryToken.Purpose.PASSWORD_RESET,
    )
    token = RecoveryToken.objects.get(identity_id=identity.identity_id)
    operation = token.operation
    leaked = leaking_recovery_provider.secret_literals

    result = deliver_recovery_operation_task.apply(
        args=[str(operation.id)],
        throw=False,
    )
    assert result.failed()

    raw_dlq = redis.xrange(settings.DLQ_STREAM_NAME)
    inspected = inspect_dead_letters()
    serialized = repr((raw_dlq, inspected, caplog.text))
    assert all(secret not in serialized for secret in leaked)
    assert "RECOVERY_DELIVERY_PROVIDER_ERROR" in serialized
    assert str(operation.id) in serialized
    assert "exception_message" not in serialized
    assert "traceback" not in serialized


@pytest.mark.parametrize(
    "password",
    [
        "short-11!!",
        "x" * 129,
        "password12345",
    ],
)
def test_password_policy_rejects_length_and_common_values(identity, password):
    with pytest.raises(ValidationError):
        validate_new_password(identity, password)


def test_password_policy_rejects_identity_attribute_similarity(identity):
    with pytest.raises(ValidationError):
        validate_new_password(identity, identity.email)
```

Extend `test_deferred_merge_convergence.py` with a parameterized canonical
password change and password-recovery completion over an
`APPROVED_DEFERRED` graph. For both operations assert one password encoding,
the identical encoded hash on canonical and every realized non-held source
shadow, one canonical security-version increment and revoked graph sessions.
Repeat with a legal hold independently on the operation, source and canonical:
the token, hashes, epochs, versions and sessions remain byte-for-byte
unchanged and exactly one idempotent `IdentityMergeHoldBlock` is appended.
Add the equivalent MFA-reset assertion: canonical factors are revoked and
source plaintext fields are untouched while only epoch/guard/session state is
invalidated. Finally, prove dormant deferred MFA confirmation/global unlock
returns `MERGE_DORMANT_FINALIZATION_REQUIRED` without consuming a challenge or
recovery token.

- [ ] **Step 2: Run and verify failure**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_identity_mfa_recovery.py apps/users/tests/test_deferred_merge_convergence.py apps/users/tests/test_deferred_merge_entrypoint_coverage.py -v
```

- [ ] **Step 3: Implement AES-256-GCM keyring**

`AuthKeyring.encrypt()` uses the active 32-byte key, a fresh 96-bit nonce and
associated data `b"complect:mfa:v1"`. `RecoveryDeliveryKeyring` uses an
independent active/previous keyring and associated data
`b"complect:recovery-delivery:v1" || operation_id.bytes || token_id.bytes ||
purpose.encode()`. Persist ciphertext, nonce and `key_id`; this binding makes
row-to-row envelope substitution fail authentication.
Decryption accepts current and configured previous keys. Keys come from Docker
Secret/Vault-backed settings, never Markdown or source.

- [ ] **Step 4: Implement challenge-bound TOTP and backup-code transitions**

Every setup/confirm/reset path first enters the shared merge coordinator; it
locks Users/operations before challenge, MFA and session rows and rechecks the
complete graph hold set. Issuing a new `MFA_SETUP` challenge then locks the
canonical Identity and any unconfirmed unrevoked TOTP credential. If its bound
challenge is expired, revoked or
consumed, the credential is CAS-revoked. If a previous setup challenge is
still live, the fresh password login CAS-revokes that challenge as
`superseded_by_fresh_login`, then revokes its credential before inserting the
new challenge. A confirmed unrevoked credential is never replaced by this
cleanup and causes `MFA_ALREADY_CONFIGURED`.

`setup_totp(identity_id, challenge_id)` repeats the stale-pending cleanup as
defense in depth, then creates at most one pending credential for the locked
`MFA_SETUP` challenge and is idempotent for that exact challenge. The unique
unrevoked-TOTP constraint therefore cannot be blocked by an abandoned setup.
`confirm_totp()` requires the same identity/challenge pair, calls
`DeferredMergeGuard.require_activation_allowed()` before consuming either
record, CAS-confirms the credential, changes `MFA_SETUP_REQUIRED -> ACTIVE`, increments
`security_version` exactly once by calling Task 5
`invalidate_identity_security_context()` with the locked expected version,
applies the synchronizer's `MfaConfirmed` effect so every source verifier can
only resolve the canonical encrypted factor, creates exactly ten backup-code
hashes and returns their plaintext values once in one transaction. No secret
or plaintext factor is copied to a source row. Replays return
`MFA_SETUP_ALREADY_CONSUMED` without codes.

Enabling/disabling/resetting MFA all call that same invalidation operation;
none separately calls a version increment or bulk revoke. Backup codes use an HMAC-SHA-256 peppered digest and constant-time
comparison; successful use performs one conditional `UPDATE ... WHERE
used_at IS NULL AND revoked_at IS NULL`. A concurrent loser receives
`BACKUP_CODE_USED`.

`mfa_credential_retention` runs daily on the `retention` queue. It first reads
a bounded PK-ordered page of candidate IDs without row locks. For each
candidate it discovers and locks the complete User/merge graph through the
shared coordinator, then takes a non-blocking work advisory and locks the
credential at the `MfaCredential` rank. Busy candidates are rescheduled; it
never target-locks or `SKIP LOCKED`s a credential before its User graph. The
dedicated retention repository purges only revoked unconfirmed credentials
older than 30 days and skips legal-hold rows. Credential revocation remains
the security decision; physical purge is never required to make a new setup
possible. A two-connection test races this worker with canonical password
reset/finalization and requires serialization or typed retry without a
database deadlock.

- [ ] **Step 5: Implement purpose-bound recovery and durable delivery**

Selector contains `recovery_id`, `purpose`, `key_id`, `issued_at` and
signature; verifier is separate random 256-bit material. DB stores only the
verifier digest and `expires_at=issued_at+30 minutes`.
`selector.recovery_id` is exactly `RecoveryToken.id`. After signature
validation the repository locks that token by ID, follows its immutable
`operation_id` OneToOne relation, then checks purpose/Identity/expiry and the
verifier in constant time. It never selects a token or operation by the
ambiguous pair `(identity_id, purpose)` during delivery or completion.

`start_recovery()` always returns `RecoveryAccepted(status="accepted")`. It
normalizes the email and selects canonical Identity rows only; deferred source
rows and their synthetic normalized values are never recovery authority. For
an unknown, ineligible or any graph-held Identity it performs the same bounded
anti-enumeration work and returns accepted without creating a token,
operation or outbox record; an internal hold denial never escapes this public
start boundary. For an eligible Identity it opens one transaction through the
shared graph coordinator that:

1. locks the Identity and CAS-revokes any previous unconsumed/unrevoked
   `RecoveryToken` for the same `(identity_id, purpose)`, even when expired;
2. follows that token's immutable `operation_id` and closes only that linked
   unfinished operation as `SUPERSEDED`, without
   changing any other historical recovery operation or clearing retained
   non-secret audit metadata;
3. allocates operation/token UUIDs, builds the selector with that token ID,
   generates the verifier and encrypts destination plus selector/verifier in
   memory with AAD bound to both IDs and purpose;
4. inserts `RecoveryOperation(status=PENDING_DELIVERY)` once with ciphertext,
   nonce, key ID and provider Message-ID already populated, then inserts the
   purpose-bound `RecoveryToken(operation=<that exact operation>)`. There is no
   temporarily envelope-less PENDING row; the partial unique token constraint,
   OneToOne constraint and deferred pair gate are the final race guards;
5. writes `auth.recovery_delivery_requested` through
   `TransactionalOutboxService.push_to_outbox(..., tenant_id=None)`.

The operation also persists the originating `request_id`, `correlation_id`
and `causal_root_id` as required non-secret UUIDs. Public request middleware
must supply them; a missing ID is an internal dependency failure before token
or operation creation, never a generated worker-local substitute.

Move the explicit-global outbox sentinel support into this task so recovery is
independently deliverable before Task 11. The ordinary outbox payload contains
only `operation_id`, purpose, event envelope and `tenant_id=None`. The API,
audit, logs, task arguments and ordinary event payload never contain the
destination, selector or verifier.

`deliver_recovery_operation(operation_id)` uses a closed delivery state
machine. It locks on primary and CAS-claims
`PENDING_DELIVERY -> DELIVERING`, writing a unique `delivery_attempt_id`, lease
expiry and incremented attempt count. In that same claim transaction it calls
`PiiAccessAuditService.append_read()` to insert one append-only
`pii.identity_email_read` record with `actor_type=system`,
`actor_ref=recovery-delivery-worker`,
`reason_code=RECOVERY_OWNER_DELIVERY`, the operation's request/correlation IDs
and only operation/attempt IDs in metadata. If the audit insert fails, the
claim rolls back and no decryption occurs. Only after this transaction commits
may the claimant decrypt destination/token material in memory and send; every
stale-lease retry that decrypts again creates its own attempt-bound read audit.
Only that claimant may decrypt and send;
a concurrent `on_commit`/outbox/Beat wakeup returns `already_claimed` without
sending. Success CASes `DELIVERING -> DELIVERED` and clears ciphertext, nonce
and key ID plus the attempt/lease fields. A retryable provider failure CASes
`DELIVERING -> PENDING_DELIVERY`, clears the attempt/lease but retains the same
envelope and immutable `provider_message_id`; a terminal failure CASes to
`FAILED`, clears the envelope/lease and revokes the linked token. A retry after
a demonstrably stale lease reclaims `DELIVERING` with a new unique attempt ID
but reuses the already-stored selector/verifier envelope and exactly the
persisted `provider_message_id`; it never generates a new token or delivery
material. Supersede/expiry similarly clears any envelope and lease after
revoking the linked token. Thus the normal path sends exactly one message, and
an ambiguous provider/crash retry can at worst resend byte-identical material
under the same provider idempotency key/Message-ID.

The Celery task receives only `str(operation_id)`; UUID objects never cross
the Celery/DLQ serialization boundary. `transaction.on_commit()` and
the outbox consumer may both wake it; the CAS above makes them safe. A
one-minute retention-queue Beat sweep retries pending or stale-delivering
operations and clears expired envelopes, so delivery does not depend on an
in-memory callback surviving. The outbox record is the durable integration
command and the operation row is the delivery-state authority.

Recovery delivery uses `RecoveryDeliveryTask(BaseEnterpriseTask)` with a
sensitive-task failure policy. Provider calls are wrapped next to `send()`;
any provider exception is converted `from None` into closed
`RecoveryDeliveryProviderError` carrying only
`RECOVERY_DELIVERY_PROVIDER_ERROR`, `provider_class`, `retryable` and
`operation_id`. `RecoveryDeliveryTask.on_failure()` never calls
`BaseEnterpriseTask.on_failure()` and never forwards the original exception,
traceback, task args or kwargs. It writes an allowlisted DLQ record containing
only task name, string operation ID, typed code, provider class, retryable,
correlation/causal IDs and retry count, then invokes incident routing with
non-secret identifiers. Captured logs use the same allowlist. This policy also
sanitizes unexpected worker faults because decrypted destination/token
material may have existed in frame locals. Replay passes only the same string
operation ID. Generic non-sensitive tasks retain the existing DLQ behavior.

Update the generic dispatcher so an explicit `payload_json["tenant_id"] is
None` routes to `erp:streams:tenant:global`, not a stream whose suffix is the
string `"None"`.

Password completion requires purpose `PASSWORD_RESET`. Before consuming the
token it discovers and locks the complete realized merge graph, checks every
hold/version/epoch, validates the password policy against the canonical
Identity and encodes the new password exactly once. In the same transaction
it performs one CAS on `consumed_at IS NULL AND revoked_at IS NULL AND
expires_at > now`, calls
`DeferredMergeCredentialSynchronizer.apply_password_hash_changed()` to write
the encoded hash to canonical plus all connected source shadows, and invokes
the merge-aware `invalidate_identity_security_context(...,
reason="password_reset")` exactly once. It then CASes only the linked operation
`DELIVERED -> COMPLETED` with `completed_at=now()`. This is the sole
version/revoke operation, so the canonical transition is exactly `N -> N+1`.

Normal owner password change is a separate credential service: it uses the
same complete graph lock, verifies only the canonical Django password hash in
constant time, validates the new password, encodes once and delegates the
canonical/source-shadow writes to the synchronizer, then calls the same
merge-aware invalidation operation once with
`reason="password_changed"` in that transaction. It
never emits `identity.password_reset`; Task 11 attaches
`identity.password_changed` to this successful transition only.

MFA completion requires purpose `MFA_RESET`, locks and hold-checks the complete
graph before token consumption, CAS-consumes the token, revokes all canonical
TOTP credentials and backup codes, changes status to `MFA_SETUP_REQUIRED`,
applies only source epoch/guard invalidation (never legacy plaintext changes),
and calls
`invalidate_identity_security_context(..., reason="mfa_reset")` once in that
transaction. It also CASes only the token's linked operation
`DELIVERED -> COMPLETED` with `completed_at=now()`. It does not separately
increment or bulk-revoke.
The next request must be a fresh password login followed by
`MFA_SETUP_PARTIAL`. Tenant-admin initiation uses the same owner email delivery;
the admin never receives the token. A concurrent loser in either purpose gets
`RECOVERY_TOKEN_USED`.

Create `validate_new_password(identity, password)`: reject length outside
12–128, then call Django `validate_password(password, user=identity)`. Configure
`MinimumLengthValidator(min_length=12)`, `CommonPasswordValidator` and
`UserAttributeSimilarityValidator`; do not add uppercase/lowercase/digit/symbol
composition rules.

- [ ] **Step 6: Backfill confirmed legacy MFA safely**

For `User.mfa_enabled=True`, decrypt/read legacy secret only inside the backfill
worker, encrypt into a confirmed `MfaCredential`, retain the legacy fields as
shadow, and never log the secret. Eligible rows finish as `ACTIVE`. An active
user without a confirmed factor finishes as `MFA_SETUP_REQUIRED` and can use
the bootstrap flow from Task 6; it is not eligible for normal `MFA_PARTIAL`.

- [ ] **Step 7: Run tests**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_identity_mfa_recovery.py apps/users/tests/test_preauth_service.py apps/users/tests/test_mfa_service.py apps/users/tests/test_deferred_merge_convergence.py apps/users/tests/test_deferred_merge_entrypoint_coverage.py apps/users/tests/test_identity_merge_locking.py apps/users/tests/test_user_admin_service.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/apps/users/services/mfa_service.py backend/apps/users/services/auth_keyring.py backend/apps/users/services/recovery_service.py backend/apps/users/services/credential_service.py backend/apps/users/services/user_admin_service.py backend/apps/users/services/deferred_merge_guard.py backend/apps/users/services/deferred_merge_credential_synchronizer.py backend/apps/users/security/identity_merge_locking.py backend/apps/users/services/pii_access_audit_service.py backend/apps/users/events.py backend/apps/users/tasks/recovery_delivery.py backend/apps/users/tasks/mfa_credential_retention.py backend/apps/users/security/password_policy.py backend/apps/users/security/credential_entrypoints.py backend/apps/users/security/credential_entrypoint_manifest.json backend/apps/common/services/outbox_service.py backend/apps/common/tasks/outbox_dispatcher.py backend/config/settings/base.py backend/apps/users/tasks/identity_backfill.py backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py backend/apps/users/tests/test_identity_mfa_recovery.py backend/apps/users/tests/test_mfa_service.py backend/apps/users/tests/test_deferred_merge_convergence.py backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py backend/apps/users/tests/test_identity_merge_locking.py backend/apps/users/tests/test_user_admin_service.py
git commit -m "feat(iam): move mfa and recovery to identity"
```

---

### Task 10: Security Controls, Cookies, Gateway and Startup Boundary

**Files:**

- Create: `backend/apps/users/security/rate_limits.py`
- Create: `backend/apps/users/security/cookie_guard.py`
- Create: `backend/apps/users/security/startup_checks.py`
- Create: `backend/apps/users/api_errors.py`
- Modify: `backend/apps/users/services/auth_service.py`
- Modify: `backend/apps/users/apps.py`
- Modify: `backend/apps/common/tenancy/middleware.py`
- Modify: `backend/apps/common/api/errors.py`
- Modify: `backend/config/api.py`
- Modify: `backend/config/settings/base.py`
- Modify: `backend/config/settings/local.py`
- Modify: `backend/config/settings/prod.py`
- Modify: `infra/api_gateway/kong.yml`
- Modify: `docker-compose.dev.yml`
- Modify: `infra/scripts/tests/test_gateway_runtime.py`
- Modify: `infra/scripts/tests/test_agent_infrastructure.py`
- Modify: `backend/apps/users/tests/conftest.py`
- Modify: `backend/apps/users/tests/iam_factories.py`
- Test: `backend/apps/users/tests/test_auth_security_controls.py`

**Interfaces:**

- Login limits: independent keys for 5 attempts per normalized email and 30 attempts per trusted gateway IP per 15 minutes.
- MFA limits: 5 attempts per challenge per 10 minutes; exhaustion revokes the challenge.
- Recovery limits: independent keys for 3 requests per normalized email and 20 per trusted gateway IP per hour.
- Cookie-auth mutations require trusted `Origin` and double-submit CSRF.
- AuthFlow errors are rendered by the users API boundary through the exhaustive
  Task 6 HTTP policy; generic Django/Ninja auth and permission errors retain
  their existing generic envelopes.
- Production startup requires auth/MFA/recovery/switch-response keyrings and
  rejects `RBAC_ENFORCEMENT_MODE=LOG_ONLY`.

- [ ] **Step 1: Write failing security-control tests**

Task 1 already created `backend/apps/users/tests/conftest.py`. Verify its
shared JSON client still has the following contract, then extend it with
`raw_client`, `authenticated_client` and the Task 10 limiter doubles. Do not
replace the existing domain factories:

```python
import json as jsonlib

import pytest
from django.test import Client
from django.test.client import MULTIPART_CONTENT

_UNSET = object()


class JsonTestClient(Client):
    def post(
        self,
        path,
        data=None,
        content_type=MULTIPART_CONTENT,
        secure=False,
        *,
        headers=None,
        query_params=None,
        json=_UNSET,
        cookies=None,
        **extra,
    ):
        if json is not _UNSET:
            if data is not None:
                raise TypeError("pass either data or json")
            data = jsonlib.dumps(json)
            content_type = "application/json"
        for name, value in (cookies or {}).items():
            self.cookies[name] = value
        return super().post(
            path,
            data=data,
            content_type=content_type,
            secure=secure,
            headers=headers,
            query_params=query_params,
            **extra,
        )


@pytest.fixture
def client(settings):
    settings.EXPECTED_GATEWAY_TOKEN = "test-gateway-token"
    return JsonTestClient(headers={"X-Gateway-Token": settings.EXPECTED_GATEWAY_TOKEN})


@pytest.fixture
def client_factory(settings):
    settings.EXPECTED_GATEWAY_TOKEN = "test-gateway-token"

    def create_client():
        return JsonTestClient(
            headers={"X-Gateway-Token": settings.EXPECTED_GATEWAY_TOKEN},
        )

    return create_client
```

This fixture remains backward-compatible with ordinary `data=` posts. It is
the documented reason subsequent snippets may use `client.post(json=...)` and
`cookies=...`; tests also assert `request.body` contains the encoded JSON.
It represents traffic already admitted by Kong and therefore supplies the
trusted gateway token by default. Gateway-trust negative tests use Django's
unwrapped `Client` under a separately named `raw_client` fixture. The shared
`authenticated_client` fixture sets a valid refresh cookie, an `auth_csrf`
cookie and `AUTH_TRUSTED_ORIGINS=["https://app.test"]`; tests must still send
the matching `Origin` and `X-Auth-CSRF` headers explicitly so a service-error
assertion cannot pass without traversing the cookie-integrity guard.

```python
from collections import Counter

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from apps.users.security.cookie_guard import (
    issue_auth_cookies,
    require_cookie_request_integrity,
)
from apps.users.security.rate_limits import AuthRateLimiter
from apps.users.security.startup_checks import validate_auth_security_settings
from apps.users.services.auth_errors import (
    AUTHENTICATION_FAILURES,
    AUTH_FAILURE_POLICIES,
    CONFLICT_FAILURES,
    REQUEST_OR_PERMISSION_FAILURES,
    TRANSIENT_INFRASTRUCTURE_FAILURES,
    VALIDATION_FAILURES,
    AuthFailureCode,
    AuthFlowError,
)


def test_cookie_guard_rejects_untrusted_origin_before_service_call(settings):
    settings.AUTH_TRUSTED_ORIGINS = ["https://app.test"]
    request = RequestFactory().post(
        "/api/v1/auth/refresh",
        HTTP_ORIGIN="https://evil.example",
    )
    request.COOKIES["auth_csrf"] = "cookie-value"
    request.META["HTTP_X_AUTH_CSRF"] = "cookie-value"

    with pytest.raises(AuthFlowError, match="AUTH_ORIGIN_REJECTED"):
        require_cookie_request_integrity(request)


@override_settings(ENVIRONMENT="production", RBAC_ENFORCEMENT_MODE="LOG_ONLY")
def test_production_startup_rejects_log_only():
    with pytest.raises(ImproperlyConfigured, match="RBAC_ENFORCEMENT_MODE"):
        validate_auth_security_settings()


def test_sixth_login_subject_attempt_is_limited_across_rotating_ips(redis):
    limiter = AuthRateLimiter(redis)
    for index in range(5):
        limiter.check_login(
            normalized_subject="limited@example.com",
            trusted_gateway_ip=f"192.0.2.{10 + index}",
        )
    with pytest.raises(AuthFlowError) as caught:
        limiter.check_login(
            normalized_subject="limited@example.com",
            trusted_gateway_ip="192.0.2.200",
        )
    assert caught.value.code is AuthFailureCode.AUTH_RATE_LIMITED
    assert caught.value.retry_after_seconds > 0


def test_forwarded_for_is_not_an_input_to_gateway_ip_limit(redis):
    limiter = AuthRateLimiter(redis)
    for index in range(30):
        limiter.check_login_ip(
            trusted_gateway_ip="192.0.2.44",
        )
    with pytest.raises(AuthFlowError, match="AUTH_RATE_LIMITED"):
        limiter.check_login_ip(trusted_gateway_ip="192.0.2.44")


@override_settings(
    ENVIRONMENT="production",
    AUTH_TRUSTED_ORIGINS=["https://app.example.com"],
)
def test_refresh_cookie_has_required_attributes():
    response = issue_auth_cookies(
        HttpResponse(),
        refresh_token="opaque-refresh",
        csrf_token="opaque-csrf",
    )
    cookie = response.cookies["refresh_token"]
    assert cookie["httponly"] is True
    assert cookie["secure"] is True
    assert cookie["samesite"] == "Strict"
    assert cookie["path"] == "/api/v1/auth/"
    assert cookie["domain"] == ""


def test_auth_failure_policy_is_exhaustive():
    assert set(AUTH_FAILURE_POLICIES) == set(AuthFailureCode)
    occurrences = Counter(
        code
        for group in (
            AUTHENTICATION_FAILURES,
            REQUEST_OR_PERMISSION_FAILURES,
            CONFLICT_FAILURES,
            TRANSIENT_INFRASTRUCTURE_FAILURES,
            frozenset({AuthFailureCode.AUTH_RATE_LIMITED}),
            VALIDATION_FAILURES,
        )
        for code in group
    )
    assert set(occurrences) == set(AuthFailureCode)
    assert all(count == 1 for count in occurrences.values())
    assert sum(occurrences.values()) == len(AuthFailureCode)


def test_limiter_outage_is_temporary_service_failure(
    unavailable_rate_limiter,
):
    with pytest.raises(AuthFlowError) as caught:
        unavailable_rate_limiter.check_login(
            normalized_subject="subject@example.com",
            trusted_gateway_ip="192.0.2.10",
        )
    assert caught.value.code is AuthFailureCode.AUTH_RATE_LIMIT_UNAVAILABLE
    assert caught.value.retry_after_seconds == 3
```

The API-level known/unknown/held login and recovery comparisons, full HTTP
rate-limit/`Retry-After` checks and cookie-route CSRF checks are added in Task
12, when those routes and request schemas actually exist. Task 10 tests the
security primitives and Kong policy without depending on future API wiring.

- [ ] **Step 2: Implement Redis Lua rate limiting**

Use separate Redis keys and counters:

- `auth:rl:login:subject:{HMAC(normalized_login_subject)}` — 5/900 seconds;
- `auth:rl:login:ip:{trusted_ip}` — 30/900 seconds;
- `auth:rl:mfa:challenge:{challenge_id}` — 5/600 seconds;
- `auth:rl:recovery:subject:{HMAC(normalized_email)}` — 3/3600 seconds;
- `auth:rl:recovery:ip:{trusted_ip}` — 20/3600 seconds.

For an email field, `normalized_login_subject` is the exact Task 2 normalized
email. For a legacy username it is trim → Unicode NFKC → casefold without
pretending that the username is an email. Subject and IP are never combined
into one key. Raw email/username/code never enters
Redis keys or logs. Increment and expiry happen in one Lua script. A Redis
outage fails closed for login, MFA and recovery with
`AuthFlowError(AuthFailureCode.AUTH_RATE_LIMIT_UNAVAILABLE)`; it never creates
a per-worker bypass.
Consume the trusted-IP limiter before parsing/normalizing the subject so
malformed identifiers still count against the IP key; consume the subject key
only after successful normalization.

Recovery subject/IP exhaustion suppresses operation creation but still returns
the same generic `202 {"status": "accepted"}` as every other recovery start.
It records only a token-free limiter decision metric. Login and MFA exhaustion
raise `AuthFlowError(AuthFailureCode.AUTH_RATE_LIMITED,
retry_after_seconds=decision.retry_after_seconds)` and return typed `429` with
that exact positive `Retry-After`.

`AsgiTenantEnforcementMiddleware` exposes `request.gateway_client_ip` only from
gateway-owned `X-Gateway-Client-IP` after successful `X-Gateway-Token`
verification. Rate-limit code never reads `X-Forwarded-For`, `Forwarded`,
`request.META["REMOTE_ADDR"]` or a body field directly.

- [ ] **Step 3: Implement Origin and CSRF guard**

For `/refresh`, `/logout`, `/logout-all`, `/tenant/select` when refresh-cookie authenticated, and `DELETE /sessions/{id}`:

```python
def require_cookie_request_integrity(request: HttpRequest) -> None:
    origin = request.headers.get("Origin")
    if origin not in settings.AUTH_TRUSTED_ORIGINS:
        raise AuthFlowError(AuthFailureCode.AUTH_ORIGIN_REJECTED)
    cookie_token = request.COOKIES.get("auth_csrf", "")
    header_token = request.headers.get("X-Auth-CSRF", "")
    if not cookie_token or not constant_time_compare(cookie_token, header_token):
        raise AuthFlowError(AuthFailureCode.CSRF_FAILED)
```

Issue `auth_csrf` as a separate random 256-bit non-HttpOnly cookie after MFA/tenant selection. Set refresh cookie: `httponly=True`, `secure=ENVIRONMENT != "local"`, `samesite="Strict"`, `path="/api/v1/auth/"`, no `domain`, max age 7 days.

Create `apps/users/api_errors.py` as the domain API adapter. It imports the
generic envelope builder from `apps.common.api.errors`, but common code never
imports the users domain. `config/api.py`, as composition root, registers the
typed handler for `AuthFlowError` before the generic `PermissionDenied`
handler. The adapter loads `AUTH_FAILURE_POLICIES[exc.code]`, preserves the
closed code in the `ErrorEnvelope`, returns its exact status and emits
`Retry-After` only when the policy resolved one. Extend the generic envelope
builder to accept already-sanitized response headers without knowing any auth
codes. Task 12 registers `AuthContextRejected` with this same adapter. Recovery
start suppression remains endpoint-specific `202`; it does not expose a 429.

- [ ] **Step 4: Add production startup checks**

`validate_auth_security_settings()` requires non-empty auth-signing, MFA,
recovery-delivery and switch-response active/previous keyrings, verifies decoded AES keys are
exactly 32 bytes, requires
`EXPECTED_GATEWAY_TOKEN` and the Vault-backed
`LEGACY_JWT_SIGNING_KEY` during the compatibility window, and rejects
`RBAC_ENFORCEMENT_MODE in {"OFF", "LOG_ONLY"}` outside local/test.
`UsersConfig.ready()` invokes it without touching DB.

Change the legacy Django JWT encode/decode calls to
`settings.LEGACY_JWT_SIGNING_KEY`. Local/test may default that setting to
`SECRET_KEY`; production must load the dedicated value from Vault/Docker
Secret. Deploy the Django and Kong values atomically.

- [ ] **Step 5: Split route-scoped Gateway policies and remove spoofable context**

Remove bearer parsing from service scope. Keep CORS service-scoped, but
preserve the existing public and documentation boundaries and define five
mutually ordered routes with exactly one route-scoped security `pre-function`
each; do not rely on stacking two plugins with the same name:

1. `public-no-user-auth` has an anchored method/path allowlist for `GET`
   health, metrics, `/api/v1/public/...`, sitemap and robots resources. A
   caller needs no bearer or gateway header. The plugin strips all supplied
   trust/context/Authorization headers, sets only Kong's upstream gateway
   token and trusted client IP, and never creates user/tenant context.
2. `docs-openapi-context-free` has `GET` anchored paths for both the deployed
   `/api/v1/docs` and `/api/v1/openapi.json` endpoints (plus any existing
   root aliases retained by configuration). It likewise requires no caller
   bearer, strips Authorization/context, and sets only upstream gateway trust.
   This route must remain separate from the public business API and protected
   catch-all.
3. `tenant-select-context-free` has `methods: [POST]`, anchored path
   `~^/api/v1/auth/tenant/select$`, `strip_path: false` and the highest explicit
   auth-route `regex_priority`. It clears all internal context headers, sets only the
   gateway token and trusted client IP, preserves raw `Authorization` byte for
   byte and performs no bearer decode. It can never set user/tenant context.
4. `auth-context-free` has a lower-priority anchored allowlist of the actual
   context-free auth endpoints, not the entire future `/auth/` namespace.
   Every entry is bound to its exact HTTP method. The Plan 1 allowlist is POST
   login; POST MFA verify/setup/confirm; POST password/MFA recovery
   start/complete; and POST refresh/logout/logout-all cookie flows.
   Tenant select is excluded because its exact route owns it. Password change,
   session listing/deletion and every unlisted future auth path fall through
   to protected access auth.
   Login/recovery start and cookie-only flows reject unexpected bearer types;
   `/mfa/verify` accepts only a live `MFA_PARTIAL` (plus legacy `mfa_pending`
   during the compatibility window), and `/mfa/setup|confirm` accepts only a
   live `MFA_SETUP_PARTIAL`. Partial tokens never set context headers. Any
   `TENANT_SELECTION` presented here is rejected before a service mutation.
5. `protected-all-routes` is the final `/` catch-all. Any bearer present must
   pass signature, expiry and `type=access` validation before the request is
   proxied. Only this route may set `X-User-ID`/`X-Tenant-ID`.

Every route policy first clears client-supplied `X-Gateway-Token`,
`X-Gateway-Client-IP`, `X-User-ID`, `X-Tenant-ID`, `X-Ctx-User-ID` and
`X-Ctx-Tenant-ID`; then it resolves the gateway secret, sets
`X-Gateway-Client-IP` only from Kong's trusted address after explicit
trusted-proxy configuration, and finally applies its token policy. Route
selection must not depend on request headers. `GET` on the exact selection
path, a trailing slash and any suffix fall through and are rejected; the
canonical API path has no trailing slash.

Protected identity-v1 access tokens require a known `kid`, valid signature,
`exp`, `iss="identity-v1"`, `aud="complect-api"`, UUID claims,
`sub == identity_id` and a well-formed `legacy_user_pk`. Compatibility tokens
without `kid` are verified only with the Vault-backed legacy signing key and
must satisfy their closed legacy access-token shape; unsigned tokens are never
accepted. Set context from verified legacy `user_id`/`tenant_id` only. The
gateway does not attempt database-backed membership validation.

Pin the Kong image version and add the signing-keyring, legacy key and
trusted-proxy environment/Vault wiring to `docker-compose.dev.yml` in the same
deployment. Shipping the protected route without the context-free partial
route allowlist would break MFA and is forbidden. Add `X-Auth-CSRF` to CORS;
credentials remain enabled only for configured application origins.

Extend the runtime upstream echo to POST, path/method/header echo and an
upstream-hit counter. Runtime tests must prove:

- unauthenticated `GET` health, metrics, one public API resource, docs and
  OpenAPI reach their intended upstream route with no user/tenant context;
- an Authorization/header spoof on those public/context-free routes is
  stripped rather than copied upstream;
- forged context headers without bearer JWT are removed;
- unsigned/tampered access JWT never reaches upstream or produces context;
- expired access JWT is rejected on a protected API;
- wrong/missing `iss`/`aud`, mismatched `sub` or malformed claims are rejected;
- forged `X-Gateway-Client-IP` is replaced by the gateway-owned address;
- valid access JWT alone produces gateway-owned user/tenant headers;
- live or expired `TENANT_SELECTION` on exact
  `POST /api/v1/auth/tenant/select` preserves
  raw Authorization, reaches upstream and produces no user/tenant headers;
- that same selection token is rejected for a protected path, `GET` exact
  path, trailing slash, suffix and every other auth endpoint, while valid MFA
  partial tokens still reach only their allowlisted endpoint.

`infra/scripts/tests/test_agent_infrastructure.py` statically asserts route
scope, keyring/proxy wiring and absence of a service-scoped bearer parser; the
runtime matrix remains authoritative. Its `compose_config()` helper must call
`docker compose --env-file /dev/null -f ... config`: the subprocess supplies
verification-only required values explicitly, and each missing-variable case
removes one from that subprocess environment. This prevents the developer
`.env` file from silently repopulating the value and turning the negative
credential tests into false failures or false greens.

- [ ] **Step 6: Run security and gateway tests**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_auth_security_controls.py apps/common/tests/test_bulletproof_tenancy.py -v
docker compose -f docker-compose.dev.yml exec backend python manage.py check --deploy
make agent-infra-test
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/users/security backend/apps/users/api_errors.py backend/apps/users/services/auth_service.py backend/apps/users/apps.py backend/apps/common/tenancy/middleware.py backend/apps/common/api/errors.py backend/config/api.py backend/config/settings/base.py backend/config/settings/local.py backend/config/settings/prod.py infra/api_gateway/kong.yml docker-compose.dev.yml infra/scripts/tests/test_gateway_runtime.py infra/scripts/tests/test_agent_infrastructure.py backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py backend/apps/users/tests/test_auth_security_controls.py
git commit -m "feat(iam): harden auth security boundary"
```

---

### Task 11: Auth Audit and Transactional Outbox Events

**Files:**

- Modify: `backend/apps/users/events.py`
- Create: `backend/apps/users/services/auth_audit_service.py`
- Modify: `backend/apps/common/services/outbox_service.py`
- Modify: auth/session/MFA/recovery services from Tasks 6–9
- Test: `backend/apps/users/tests/test_auth_audit_events.py`

**Interfaces:**

- Produces events `identity.created`, `identity.password_changed`,
  `identity.password_reset`, `auth.login_failed`, `auth.login_succeeded`,
  `auth.refresh_reuse_detected`, `session.revoked`, `identity.mfa_enabled`,
  `identity.mfa_reset`.
- Produces one-migration-window aliases `user.created`,
  `user.password_changed`, `user.password_reset` and `user.mfa_enabled` for
  their corresponding `identity.*` canonical events. A legacy
  `user.mfa_disabled` alias for canonical `identity.mfa_reset` is emitted only
  if the signed consumer inventory identifies a real owner that cannot migrate
  in this release.
  Alias payloads carry `compatibility_alias_of` and `canonical_event_id`.
- Produces DB-enforced append-only `IdentitySecurityAuditRecord`; only the
  explicit monotonic legal-hold latch from Task 1 is mutable.
- Allows global outbox payloads with `tenant_id=None`; no nil/dummy tenant is fabricated.

- [ ] **Step 1: Write failing audit/outbox tests**

```python
import json

import pytest
from django.db import DatabaseError, transaction

from apps.common.models.outbox import OutboxRecord
from apps.users.events import emit_auth_event
from apps.users.contracts.user_info_impl import DjangoUserInfoProvider
from apps.users.models import IdentityMergeOperation, IdentitySecurityAuditRecord
from apps.users.services.auth_errors import AuthFlowError
from apps.users.services.credential_service import change_password
from apps.users.services.preauth_service import LoginIdentifier, verify_password
from apps.users.services.recovery_service import (
    complete_mfa_recovery,
    complete_password_recovery,
)
from apps.users.services.session_service import revoke_session
from apps.users.tasks.identity_backfill import run_identity_backfill_batch


@pytest.mark.django_db(transaction=True)
def test_failed_login_audits_pseudonymized_subject_without_raw_email():
    with pytest.raises(AuthFlowError):
        verify_password(
            LoginIdentifier(kind="email", value="unknown@example.com"),
            "wrong",
            {},
        )

    audit = IdentitySecurityAuditRecord.objects.get(event_type="auth.login_failed")
    assert audit.subject_hmac
    assert "unknown@example.com" not in json.dumps(audit.metadata)


@pytest.mark.django_db(transaction=True)
def test_session_revoke_and_outbox_commit_together(identity_session):
    revoke_session(identity_session.id, identity_session.identity_id, "user_request")

    assert IdentitySecurityAuditRecord.objects.filter(
        event_type="session.revoked",
        session_id=identity_session.id,
    ).exists()
    assert OutboxRecord.objects.filter(event_type="session.revoked").exists()


@pytest.mark.django_db(transaction=True)
def test_outbox_failure_rolls_back_session_revoke_and_audit(
    identity_session,
    monkeypatch,
):
    def fail_outbox(*args, **kwargs):
        raise RuntimeError("injected_outbox_failure")

    monkeypatch.setattr(
        "apps.users.services.auth_audit_service.push_to_outbox",
        fail_outbox,
    )

    with pytest.raises(RuntimeError, match="injected_outbox_failure"):
        revoke_session(
            identity_session.id,
            identity_session.identity_id,
            "user_request",
        )

    identity_session.refresh_from_db()
    assert identity_session.revoked_at is None
    assert not IdentitySecurityAuditRecord.objects.filter(
        event_type="session.revoked",
        session_id=identity_session.id,
    ).exists()
    assert not OutboxRecord.objects.filter(
        event_type="session.revoked",
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_identity_event_emits_correlated_user_compatibility_alias(
    identity_mfa_enabled_event,
):
    emit_auth_event(identity_mfa_enabled_event)

    canonical = OutboxRecord.objects.get(event_type="identity.mfa_enabled")
    alias = OutboxRecord.objects.get(event_type="user.mfa_enabled")
    canonical_id = canonical.payload_json["event_id"]
    assert alias.payload_json["compatibility_alias_of"] == "identity.mfa_enabled"
    assert alias.payload_json["canonical_event_id"] == canonical_id
    assert alias.correlation_id == canonical.correlation_id
    assert alias.causal_root_id == canonical.causal_root_id


@pytest.mark.django_db(transaction=True)
def test_live_identity_creation_emits_created_but_backfill_does_not(
    legacy_user_without_identity_foundation,
    live_identity_factory,
):
    live = live_identity_factory(email="new-identity@example.com")
    assert OutboxRecord.objects.filter(
        event_type="identity.created",
        payload_json__identity_id=str(live.identity_id),
    ).count() == 1

    run_identity_backfill_batch()
    legacy_user_without_identity_foundation.refresh_from_db()
    assert not OutboxRecord.objects.filter(
        event_type="identity.created",
        payload_json__identity_id=str(
            legacy_user_without_identity_foundation.identity_id,
        ),
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_normal_change_and_recovery_emit_distinct_password_events(
    password_change_identity,
    recovery_identity,
    delivered_password_recovery,
):
    change_password(
        password_change_identity.identity_id,
        current_password="Pass1!",
        new_password="Changed-Password-1!",
    )
    assert OutboxRecord.objects.filter(
        event_type="identity.password_changed",
    ).count() == 1
    assert not OutboxRecord.objects.filter(
        event_type="identity.password_reset",
    ).exists()

    material = delivered_password_recovery(recovery_identity)
    complete_password_recovery(
        material.selector,
        material.verifier,
        "Recovered-Password-1!",
    )
    assert OutboxRecord.objects.filter(
        event_type="identity.password_reset",
    ).count() == 1


@pytest.mark.django_db(transaction=True)
def test_mfa_recovery_emits_reset_not_disabled(
    delivered_mfa_recovery,
):
    complete_mfa_recovery(
        delivered_mfa_recovery.selector,
        delivered_mfa_recovery.verifier,
    )
    assert OutboxRecord.objects.filter(event_type="identity.mfa_reset").count() == 1
    assert not OutboxRecord.objects.filter(
        event_type="identity.mfa_disabled",
    ).exists()
    assert not OutboxRecord.objects.filter(event_type="user.mfa_disabled").exists()


@pytest.mark.django_db(transaction=True)
def test_identity_security_audit_payload_is_database_append_only(audit_record):
    with pytest.raises(DatabaseError, match="identity_audit_append_only"):
        with transaction.atomic():
            IdentitySecurityAuditRecord.objects.filter(pk=audit_record.pk).update(
                metadata={"rewritten": True},
            )
    with pytest.raises(DatabaseError, match="identity_audit_append_only"):
        with transaction.atomic():
            IdentitySecurityAuditRecord.objects.filter(pk=audit_record.pk).delete()


@pytest.mark.django_db(transaction=True)
def test_identity_security_audit_allows_only_monotonic_legal_hold_latch(
    audit_record,
    audit_record_factory,
):
    assert IdentitySecurityAuditRecord.objects.filter(pk=audit_record.pk).update(
        is_legal_hold=True,
    ) == 1
    with pytest.raises(DatabaseError, match="identity_audit_append_only"):
        with transaction.atomic():
            IdentitySecurityAuditRecord.objects.filter(pk=audit_record.pk).update(
                is_legal_hold=False,
            )
    second = audit_record_factory(metadata={"original": True})
    with pytest.raises(DatabaseError, match="identity_audit_append_only"):
        with transaction.atomic():
            IdentitySecurityAuditRecord.objects.filter(pk=second.pk).update(
                is_legal_hold=True,
                metadata={"rewritten": True},
            )
```

- [ ] **Step 2: Define immutable event dataclasses**

```python
@dataclass(slots=True, frozen=True)
class SessionRevoked:
    EVENT_TYPE = "session.revoked"
    event_id: uuid.UUID
    event_version: int
    occurred_at: datetime
    correlation_id: uuid.UUID
    identity_id: uuid.UUID
    session_id: uuid.UUID
    reason: str
```

Use the same envelope for all listed events. Failed-login events contain a subject HMAC, never raw email.

For one migration window use this fixed mapping:

```python
IDENTITY_EVENT_COMPATIBILITY_ALIASES = {
    "identity.created": "user.created",
    "identity.password_changed": "user.password_changed",
    "identity.password_reset": "user.password_reset",
    "identity.mfa_enabled": "user.mfa_enabled",
}
```

Before adding any MFA-reset alias, inventory repository consumers, webhook
registries and named external Redis/integration owners. The current repository
has no producer, registered webhook or consumer for `user.mfa_disabled`, so it
is not a default alias. If external inventory proves a temporary dependency,
record its owner/removal release and add exactly
`"identity.mfa_reset": "user.mfa_disabled"` for that migration window; never
restore `identity.mfa_disabled` as a canonical event.

The canonical and alias outbox records are inserted in the same transaction.
`OutboxRecord.id` remains the dispatch-record identifier and is not assumed to
equal the domain event ID stored in `payload_json["event_id"]`. The alias uses
a new domain event ID, carries
`compatibility_alias_of=<canonical event type>` and
`canonical_event_id=<canonical event ID>`, and otherwise preserves correlation
and causal root. New consumers must not subscribe to the aliases. Their removal
is a separately announced contract step after the migration window.

- [ ] **Step 3: Preserve the explicit global outbox contract**

Task 9 already adds a sentinel so existing tenant events keep deriving tenant
context while an explicitly global event serializes `tenant_id=None`. Keep the
exact interface while adding canonical/alias emission:

```python
_TENANT_FROM_CONTEXT = object()


def push_to_outbox(
    event,
    *,
    tenant_id: uuid.UUID | None | object = _TENANT_FROM_CONTEXT,
    causal_root_id: uuid.UUID | None = None,
) -> OutboxRecord:
    if tenant_id is _TENANT_FROM_CONTEXT:
        tenant_id = get_current_tenant_id()
    payload["tenant_id"] = str(tenant_id) if tenant_id is not None else None
```

Do not substitute the nil UUID.

- [ ] **Step 4: Emit audit and outbox records transactionally at exact boundaries**

Successful login, MFA/recovery mutations, reuse revoke and session revoke insert audit/outbox records inside the same `transaction.atomic()` as the security transition. Invalid login uses its own short transaction because there is no business mutation. Dispatch remains `transaction.on_commit()`.

The three creation/password emit points are non-overlapping:

- `identity.created`: only the live path that inserts a brand-new global
  `users.User`/Identity after Task 3 dual-write is enabled; migration/backfill,
  collision merge and tombstone creation never emit it;
- `identity.password_changed`: only Task 9
  `credential_service.change_password()` after owner authentication and a
  successful normal password mutation;
- `identity.password_reset`: only successful `PASSWORD_RESET` recovery-token
  consumption.

Each canonical event and its temporary alias are inserted in the same DB
transaction as its source mutation. Tests assert one canonical event and no
event from the neighboring boundary.

- [ ] **Step 5: Run tests**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_auth_audit_events.py apps/common/tests/test_outbox_reliability.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/users/events.py backend/apps/users/services/auth_audit_service.py backend/apps/users/services/preauth_service.py backend/apps/users/services/refresh_rotation_service.py backend/apps/users/services/session_service.py backend/apps/users/services/tenant_switch_service.py backend/apps/users/services/mfa_service.py backend/apps/users/services/recovery_service.py backend/apps/users/services/credential_service.py backend/apps/common/services/outbox_service.py backend/apps/users/tests/test_auth_audit_events.py backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py
git commit -m "feat(iam): audit identity auth events"
```

---

### Task 12: API Compatibility, Typed ErrorEnvelope and Browser Flow

**Files:**

- Modify: `backend/apps/users/api.py`
- Modify: `backend/apps/users/schemas/auth.py`
- Modify: `backend/apps/common/api/auth.py`
- Modify: `backend/apps/common/api/errors.py`
- Create: `backend/apps/common/contracts/auth_context.py`
- Test: `backend/apps/common/tests/test_auth_context_boundary.py`
- Modify: `backend/apps/users/api_errors.py`
- Modify: `backend/config/api.py`
- Modify: `backend/config/urls.py`
- Modify: `backend/config/settings/base.py`
- Create: `backend/openapi_master.json` from the pre-change
  `frontend/openapi.json`
- Modify: `infra/scripts/openapi_contract_linter.py`
- Create: `infra/scripts/tests/test_openapi_contract_linter.py`
- Create: `backend/apps/users/services/auth_context_validator.py`
- Create: `backend/apps/users/observability.py`
- Modify: `backend/apps/users/security/credential_entrypoints.py`
- Modify: `backend/apps/users/security/credential_entrypoint_manifest.json`
- Test: `backend/apps/users/tests/test_identity_auth_api.py`
- Test: `backend/apps/users/tests/test_identity_auth_openapi.py`
- Test: `backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py`
- Modify: `frontend/src/shared/auth/api.ts`
- Modify: `frontend/src/shared/auth/store.ts`
- Modify: `frontend/src/app/(auth)/login/page.tsx`
- Create: `frontend/src/app/(auth)/login/mfa-setup/page.tsx`
- Create: `frontend/src/app/(auth)/login/mfa/page.tsx`
- Create: `frontend/src/app/(auth)/login/tenant/page.tsx`
- Create: `frontend/src/app/(auth)/login/recovery/password/page.tsx`
- Create: `frontend/src/app/(auth)/login/recovery/mfa/page.tsx`
- Test: `frontend/src/shared/auth/store.test.ts`

**Interfaces:**

- `POST /api/v1/auth/login`: legacy `username` or new optional `email` plus
  password returns the frozen `TokenResponse`. Its required `access_token`
  contains `MFA_PARTIAL` or `MFA_SETUP_PARTIAL`, `mfa_required=True`, and the
  additive optional `next_step` identifies the branch; it never contains an
  access-type token or sets a refresh cookie before MFA.
- `POST /api/v1/auth/mfa/setup` preserves
  `MFASetupResponse(secret, otpauth_url)`. `/mfa/confirm` preserves request
  field `totp_code` and required `MFAStatusResponse.mfa_enabled`, adds only
  optional backup/post-MFA fields, returns ten backup codes once, then
  continues through tenant selection.
- `POST /api/v1/auth/mfa/verify` preserves request field `totp_code` and frozen
  `TokenResponse`: access token for one membership, or a `TENANT_SELECTION`
  token in the historical `access_token` field for several.
- `POST /api/v1/auth/tenant/select`: idempotent initial selection/switch.
- `POST /api/v1/auth/refresh`, `/logout`, `/logout-all`.
- `POST /api/v1/auth/password/change`: owner-authenticated current-password
  change; revokes all sessions and clears auth cookies.
- Registers the concrete API adapters for login, MFA, refresh/logout-all,
  password change and both recovery purposes in the entry-point inventory;
  scanner coverage and a fresh exact-build signature are release gates, not a
  later operational follow-up.
- `GET /api/v1/auth/sessions`, `DELETE /api/v1/auth/sessions/{session_id}`.
- `POST /api/v1/auth/recovery/password/start`,
  `/recovery/password/complete`, `/recovery/mfa/start` and
  `/recovery/mfa/complete` use Task 9 services. Start responses are always the
  same generic `202` and never contain delivery material.
- Every route declares an exact Django Ninja `response={...}` map containing
  its successful response, reachable typed errors and any pre-existing v1
  status that ADR-0003 requires retaining during the compatibility window.
  Runtime exception-handler mappings alone are not accepted as OpenAPI
  evidence.
- ADR-0003 is a hard gate: no existing v1 request/response property is removed
  or renamed, no existing property type is changed and no new request field is
  required. Frozen response DTOs may not lose required fields; optional
  response additions are allowed. A strengthened frozen-schema linter
  compares generated OpenAPI with `frontend/openapi.json` before SDK
  generation.
- Produces a frozen common `AuthenticatedContext` DTO and
  `AuthContextValidationProtocol`; `apps.common.api.auth.JWTAuth` is configured
  exactly once by `config.api` with the users validator and fails closed if
  used before registration. Common code never imports `apps.users`.

- [ ] **Step 1: Write failing API flow tests**

Before modifying any schema or route, copy the current tracked
`frontend/openapi.json` to `backend/openapi_master.json` and verify their
SHA-256 hashes match. This snapshot is part of the Task 12 change and must not
be regenerated from the new routes.

```python
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid

import pyotp
import pytest
from django.db import close_old_connections
from django.test import override_settings

from apps.common.models.outbox import OutboxRecord
from apps.users.models import (
    IdentitySession,
    PreAuthChallenge,
    RecoveryOperation,
    RecoveryToken,
    User,
)
from apps.users.services.auth_context_validator import (
    AuthContextRejected,
    AuthContextValidator,
)
from apps.users.services.auth_errors import AuthFailureCode
from apps.users.services.auth_service import _pk_to_uuid4
from apps.users.services.auth_token_codec import decode_auth_token
from apps.users.tests.iam_factories import (
    current_totp,
    login_partial,
    replay_switch_result,
)


@pytest.mark.django_db(transaction=True)
def test_login_requires_mfa_before_any_session_or_cookie(client, identity_with_totp):
    response = client.post("/api/v1/auth/login", json={"email": identity_with_totp.normalized_email, "password": "Pass1!"})
    assert response.status_code == 200
    assert response.json()["next_step"] == "mfa"
    assert response.json()["mfa_required"] is True
    assert decode_auth_token(
        response.json()["access_token"],
        expected_type="MFA_PARTIAL",
    )["identity_id"] == str(identity_with_totp.identity_id)
    assert "refresh_token" not in response.cookies
    assert not IdentitySession.objects.filter(identity_id=identity_with_totp.identity_id).exists()


@pytest.mark.django_db(transaction=True)
def test_legacy_username_login_input_remains_accepted(client, identity_with_totp):
    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": identity_with_totp.username,
            "password": "Pass1!",
        },
    )
    assert response.status_code == 200
    assert response.json()["next_step"] == "mfa"
    assert response.json()["mfa_required"] is True
    assert response.json()["token_type"] == "bearer"
    assert response.json()["permissions"] == []


@pytest.mark.django_db(transaction=True)
def test_dual_identifier_input_is_uniformly_rejected_before_lookup(
    client,
    identity_with_totp,
):
    responses = []
    for candidate_email in (
        identity_with_totp.normalized_email,
        "absent-subject@example.invalid",
    ):
        response = client.post(
            "/api/v1/auth/login",
            json={
                "username": identity_with_totp.username,
                "email": candidate_email,
                "password": "deliberately-wrong",
            },
        )
        responses.append((response.status_code, response.json()))

    assert responses[0] == responses[1]
    assert responses[0][0] == 422
    assert responses[0][1]["error"]["code"] == "AMBIGUOUS_IDENTITY_INPUT"


@pytest.mark.django_db(transaction=True)
def test_known_unknown_and_held_recovery_start_are_identical(
    gateway_client,
    identity,
):
    unknown = gateway_client.post(
        "/api/v1/auth/recovery/password/start",
        json={"email": "unknown@example.invalid"},
    )
    known = gateway_client.post(
        "/api/v1/auth/recovery/password/start",
        json={"email": identity.normalized_email},
    )
    User.objects.filter(pk=identity.pk).update(identity_legal_hold=True)
    held = gateway_client.post(
        "/api/v1/auth/recovery/mfa/start",
        json={"email": identity.normalized_email},
    )

    assert (unknown.status_code, unknown.json()) == (
        known.status_code,
        known.json(),
    ) == (held.status_code, held.json()) == (202, {"status": "accepted"})
    assert not RecoveryToken.objects.filter(
        identity_id=identity.identity_id,
        purpose=RecoveryToken.Purpose.MFA_RESET,
    ).exists()
    assert not RecoveryOperation.objects.filter(
        token__identity_id=identity.identity_id,
        token__purpose=RecoveryToken.Purpose.MFA_RESET,
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_held_and_unknown_login_are_indistinguishable_without_challenge(
    gateway_client,
    identity,
):
    User.objects.filter(pk=identity.pk).update(identity_legal_hold=True)
    held = gateway_client.post(
        "/api/v1/auth/login",
        json={"email": identity.normalized_email, "password": "Pass1!"},
    )
    unknown = gateway_client.post(
        "/api/v1/auth/login",
        json={"email": "held-probe@example.invalid", "password": "Pass1!"},
    )
    assert (held.status_code, held.json()) == (
        unknown.status_code,
        unknown.json(),
    )
    assert held.status_code == 401
    assert not PreAuthChallenge.objects.filter(
        identity_id=identity.identity_id,
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_sixth_http_login_attempt_returns_typed_429(
    gateway_client,
    isolated_auth_rate_limits,
):
    for index in range(5):
        response = gateway_client.post(
            "/api/v1/auth/login",
            json={"email": "limited@example.com", "password": "wrong"},
            gateway_client_ip=f"192.0.2.{10 + index}",
        )
        assert response.status_code == 401
    blocked = gateway_client.post(
        "/api/v1/auth/login",
        json={"email": "limited@example.com", "password": "wrong"},
        gateway_client_ip="192.0.2.200",
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "AUTH_RATE_LIMITED"
    assert int(blocked.headers["Retry-After"]) > 0


def test_login_limiter_outage_is_typed_http_503(
    gateway_client,
    force_rate_limiter_unavailable,
):
    force_rate_limiter_unavailable()
    response = gateway_client.post(
        "/api/v1/auth/login",
        json={"email": "subject@example.com", "password": "wrong"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_RATE_LIMIT_UNAVAILABLE"
    assert response.headers["Retry-After"] == "3"


def test_refresh_http_rejects_untrusted_origin_before_rotation(
    authenticated_client,
):
    response = authenticated_client.post(
        "/api/v1/auth/refresh",
        headers={
            "Origin": "https://evil.example",
            "X-Auth-CSRF": authenticated_client.cookies["auth_csrf"].value,
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUTH_ORIGIN_REJECTED"


@pytest.mark.django_db(transaction=True)
def test_identity_without_factor_receives_setup_step_and_no_session(
    client,
    identity_without_factor,
):
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": identity_without_factor.normalized_email,
            "password": "Pass1!",
        },
    )
    assert response.status_code == 200
    assert response.json()["next_step"] == "mfa_setup"
    assert response.json()["mfa_required"] is True
    decode_auth_token(
        response.json()["access_token"],
        expected_type="MFA_SETUP_PARTIAL",
    )
    assert "refresh_token" not in response.cookies
    assert not IdentitySession.objects.filter(
        identity_id=identity_without_factor.identity_id,
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_setup_partial_enrolls_before_any_session(client, identity_without_factor):
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": identity_without_factor.normalized_email,
            "password": "Pass1!",
        },
    )
    partial = login.json()["access_token"]
    setup = client.post(
        "/api/v1/auth/mfa/setup",
        headers={"Authorization": f"Bearer {partial}"},
    )
    assert setup.status_code == 200
    assert not IdentitySession.objects.filter(
        identity_id=identity_without_factor.identity_id,
    ).exists()

    confirmed = client.post(
        "/api/v1/auth/mfa/confirm",
        json={"totp_code": pyotp.TOTP(setup.json()["secret"]).now()},
        headers={"Authorization": f"Bearer {partial}"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["mfa_enabled"] is True
    assert len(confirmed.json()["backup_codes"]) == 10


@pytest.mark.django_db(transaction=True)
def test_login_ignores_extra_tenant_field_and_never_uses_it(
    client,
    identity_with_totp,
):
    supplied_tenant = uuid.uuid4()
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": identity_with_totp.normalized_email,
            "password": "Pass1!",
            "tenant_id": str(supplied_tenant),
        },
    )
    assert response.status_code == 200
    claims = decode_auth_token(
        response.json()["access_token"],
        expected_type="MFA_PARTIAL",
    )
    assert "tenant_id" not in claims
    assert not IdentitySession.objects.filter(identity_id=identity_with_totp.identity_id).exists()


@pytest.mark.django_db(transaction=True)
def test_multiple_memberships_require_selection_after_mfa(client, identity_with_two_memberships):
    partial = login_partial(client, identity_with_two_memberships)
    response = client.post(
        "/api/v1/auth/mfa/verify",
        json={"totp_code": current_totp(identity_with_two_memberships)},
        headers={"Authorization": f"Bearer {partial}"},
    )
    assert response.json()["next_step"] == "tenant_selection"
    assert response.json()["mfa_required"] is True
    decode_auth_token(
        response.json()["access_token"],
        expected_type="TENANT_SELECTION",
    )
    assert "refresh_token" not in response.cookies


@pytest.mark.django_db(transaction=True)
def test_single_membership_mfa_keeps_frozen_token_response(
    client,
    identity_with_one_membership,
):
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": identity_with_one_membership.normalized_email,
            "password": "Pass1!",
        },
    )
    response = client.post(
        "/api/v1/auth/mfa/verify",
        json={"totp_code": current_totp(identity_with_one_membership)},
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["token_type"] == "bearer"
    assert payload["mfa_required"] is False
    assert isinstance(payload["permissions"], list)
    assert payload["next_step"] == "authenticated"
    decode_auth_token(payload["access_token"], expected_type="access")
    assert response.cookies["refresh_token"].value


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_http_tenant_select_replays_return_identical_response(
    client_factory,
    tenant_selection_token,
    selectable_membership,
):
    operation_id = uuid.uuid4()
    barrier = Barrier(2)

    def select():
        close_old_connections()
        try:
            client = client_factory()
            barrier.wait()
            response = client.post(
                "/api/v1/auth/tenant/select",
                json={"tenant_id": str(selectable_membership.tenant_id)},
                headers={
                    "Authorization": f"Bearer {tenant_selection_token}",
                    "Idempotency-Key": str(operation_id),
                },
            )
            return (
                response.status_code,
                response.json(),
                response.cookies["refresh_token"].value,
                response.cookies["auth_csrf"].value,
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: select(), range(2)))
    assert first == second


@pytest.mark.django_db(transaction=True)
def test_tenant_select_replay_after_lost_http_response_returns_original_tokens(
    client_factory,
    tenant_selection_token,
    selectable_membership,
):
    operation_id = uuid.uuid4()
    headers = {
        "Authorization": f"Bearer {tenant_selection_token}",
        "Idempotency-Key": str(operation_id),
    }
    first_client = client_factory()
    first = first_client.post(
        "/api/v1/auth/tenant/select",
        json={"tenant_id": str(selectable_membership.tenant_id)},
        headers=headers,
    )
    first_result = (
        first.json(),
        first.cookies["refresh_token"].value,
        first.cookies["auth_csrf"].value,
    )
    # Simulate transport loss with a fresh client that has none of the first
    # response cookies; replay must stay on the bearer/idempotency path.
    replay_client = client_factory()
    assert "refresh_token" not in replay_client.cookies
    assert "auth_csrf" not in replay_client.cookies
    replay = replay_client.post(
        "/api/v1/auth/tenant/select",
        json={"tenant_id": str(selectable_membership.tenant_id)},
        headers=headers,
    )
    assert (
        replay.json(),
        replay.cookies["refresh_token"].value,
        replay.cookies["auth_csrf"].value,
    ) == first_result


@pytest.mark.django_db(transaction=True)
def test_session_delete_hides_foreign_session_and_revokes_own(
    authenticated_client,
    authenticated_identity_session,
    other_identity_session,
):
    headers = {
        "Origin": "https://app.test",
        "X-Auth-CSRF": authenticated_client.cookies["auth_csrf"].value,
    }
    foreign = authenticated_client.delete(
        f"/api/v1/auth/sessions/{other_identity_session.id}",
        headers=headers,
    )
    assert foreign.status_code == 404
    other_identity_session.refresh_from_db()
    assert other_identity_session.revoked_at is None

    own = authenticated_client.delete(
        f"/api/v1/auth/sessions/{authenticated_identity_session.id}",
        headers=headers,
    )
    assert own.status_code == 204
    authenticated_identity_session.refresh_from_db()
    assert authenticated_identity_session.revoked_at is not None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("sub", str(uuid.uuid4())),
        ("identity_id", str(uuid.uuid4())),
        ("legacy_user_pk", 999999),
        ("user_id", str(uuid.uuid4())),
    ],
)
def test_mismatched_identity_claims_fail_before_permissions(
    identity_access_claims,
    signed_access_token,
    claim,
    value,
):
    claims = {**identity_access_claims, claim: value}
    token = signed_access_token(claims)
    with pytest.raises(AuthContextRejected, match="IDENTITY_CLAIMS_MISMATCH"):
        AuthContextValidator().validate(token)


@pytest.mark.django_db(transaction=True)
def test_merged_membership_claims_keep_canonical_identity_and_tenant_actor(
    merged_identity_access_context,
):
    claims = merged_identity_access_context.claims
    membership = merged_identity_access_context.membership
    canonical = merged_identity_access_context.canonical_identity
    source_actor = merged_identity_access_context.source_actor

    assert claims["identity_id"] == claims["sub"] == str(canonical.identity_id)
    assert claims["legacy_user_pk"] == source_actor.pk
    assert claims["user_id"] == _pk_to_uuid4(source_actor.pk)
    assert membership.identity_id == canonical.identity_id
    assert membership.legacy_actor_user_id == source_actor.pk
    context = AuthContextValidator().validate(
        merged_identity_access_context.token,
    )
    assert context.identity_id == canonical.identity_id
    assert context.legacy_actor_user_pk == source_actor.pk
    assert context.actor_user_id == uuid.UUID(_pk_to_uuid4(source_actor.pk))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "claim",
    ["session_id", "membership_id", "tenant_id"],
)
def test_mismatched_grant_binding_fails_before_permissions(
    identity_access_claims,
    signed_access_token,
    claim,
):
    token = signed_access_token(
        {**identity_access_claims, claim: str(uuid.uuid4())},
    )
    with pytest.raises(AuthContextRejected, match="TENANT_CONTEXT_MISMATCH"):
        AuthContextValidator().validate(token)


@pytest.mark.django_db(transaction=True)
def test_recovery_start_never_returns_secret(client, identity):
    response = client.post(
        "/api/v1/auth/recovery/password/start",
        json={"email": identity.normalized_email},
    )
    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    assert "selector" not in response.content.decode()
    assert "verifier" not in response.content.decode()


@pytest.mark.django_db(transaction=True)
def test_owner_password_change_is_not_password_recovery(authenticated_client):
    response = authenticated_client.post(
        "/api/v1/auth/password/change",
        json={
            "current_password": "Pass1!",
            "new_password": "Changed-Password-1!",
        },
    )
    assert response.status_code == 204
    assert response.cookies["refresh_token"]["max-age"] == 0
    assert OutboxRecord.objects.filter(
        event_type="identity.password_changed",
    ).count() == 1
    assert not OutboxRecord.objects.filter(
        event_type="identity.password_reset",
    ).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("operation_state", ["unknown", "pending"])
def test_expired_tenant_selection_without_result_has_closed_http_error(
    client,
    expired_tenant_selection_token,
    operation_state,
    pending_operation_factory,
):
    operation_id = uuid.uuid4()
    if operation_state == "pending":
        pending_operation_factory(operation_id=operation_id)
    response = client.post(
        "/api/v1/auth/tenant/select",
        json={"tenant_id": str(uuid.uuid4())},
        headers={
            "Authorization": f"Bearer {expired_tenant_selection_token}",
            "Idempotency-Key": str(operation_id),
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TENANT_SELECTION_EXPIRED"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "code",
    [
        AuthFailureCode.SESSION_RECONCILIATION_REQUIRED,
        AuthFailureCode.TENANT_SWITCH_RECONCILIATION_REQUIRED,
        AuthFailureCode.AUTH_DEPENDENCY_UNAVAILABLE,
    ],
)
def test_transient_auth_failure_is_503_with_retry_after(
    authenticated_client,
    force_auth_service_failure,
    code,
):
    force_auth_service_failure(code)
    csrf = authenticated_client.cookies["auth_csrf"].value
    response = authenticated_client.post(
        "/api/v1/auth/refresh",
        headers={
            "Origin": "https://app.test",
            "X-Auth-CSRF": csrf,
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == code.value
    assert response.headers["Retry-After"] == "3"


def test_idempotency_result_expired_is_409_without_retry_after(
    client,
    expired_switch_result,
):
    response = replay_switch_result(client, expired_switch_result)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_RESULT_EXPIRED"
    assert "Retry-After" not in response.headers


@override_settings(IAM_ALLOW_REFRESH_BODY_COMPAT=True)
def test_refresh_body_compatibility_records_usage_without_token(
    client,
    valid_refresh_token,
    metrics_registry,
):
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": valid_refresh_token},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.json()["token_type"] == "bearer"
    assert response.json()["mfa_required"] is False
    assert isinstance(response.json()["permissions"], list)
    assert metrics_registry.value("iam_refresh_token_body_compat_total") == 1
    assert valid_refresh_token not in metrics_registry.render()


@override_settings(IAM_ALLOW_REFRESH_BODY_COMPAT=False)
def test_refresh_body_compatibility_can_be_disabled(client, valid_refresh_token):
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": valid_refresh_token},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "REFRESH_COOKIE_REQUIRED"
```

Create `backend/apps/users/tests/test_identity_auth_openapi.py`:

```python
import json
from pathlib import Path

from django.conf import settings
from config.urls import api


SCHEMA_SHAPE_KEYS = frozenset(
    {"type", "format", "$ref", "items", "anyOf", "oneOf", "allOf"},
)


def frozen_schema_shape(node):
    if isinstance(node, list):
        return [frozen_schema_shape(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {
        key: frozen_schema_shape(value)
        for key, value in node.items()
        if key in SCHEMA_SHAPE_KEYS
    }


def assert_error_envelope(operation: dict, status: int) -> None:
    response = operation["responses"][str(status)]
    assert "#/components/schemas/ErrorEnvelope" in json.dumps(response)


def test_auth_openapi_declares_reachable_typed_failures():
    schema = api.get_openapi_schema()
    paths = schema["paths"]

    login = paths["/api/v1/auth/login"]["post"]
    assert set(login["responses"]) >= {"200", "401", "422", "429", "503"}
    for status in (401, 422, 429, 503):
        assert_error_envelope(login, status)

    tenant_select = paths["/api/v1/auth/tenant/select"]["post"]
    assert set(tenant_select["responses"]) >= {
        "200",
        "401",
        "403",
        "409",
        "503",
    }
    for status in (401, 403, 409, 503):
        assert_error_envelope(tenant_select, status)

    refresh = paths["/api/v1/auth/refresh"]["post"]
    assert set(refresh["responses"]) >= {"200", "401", "403", "503"}
    for status in (401, 403, 503):
        assert_error_envelope(refresh, status)


def test_recovery_start_suppresses_threshold_429_in_openapi():
    operation = api.get_openapi_schema()["paths"][
        "/api/v1/auth/recovery/password/start"
    ]["post"]
    assert "202" in operation["responses"]
    assert "429" not in operation["responses"]
    assert_error_envelope(operation, 503)


def test_every_declared_auth_error_uses_typed_envelope():
    paths = api.get_openapi_schema()["paths"]
    for path, path_item in paths.items():
        if not path.startswith("/api/v1/auth/"):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            for status in operation["responses"]:
                if int(status) >= 400:
                    assert_error_envelope(operation, int(status))


def test_frozen_v1_auth_dtos_and_success_refs_are_additive_only():
    baseline = json.loads(
        (Path(settings.BASE_DIR) / "openapi_master.json").read_text(),
    )
    current = api.get_openapi_schema()
    request_names = {
        "LoginRequest",
        "MFAVerifyRequest",
        "RefreshRequest",
    }
    response_names = {
        "TokenResponse",
        "MFASetupResponse",
        "MFAStatusResponse",
    }
    for name in request_names | response_names:
        old = baseline["components"]["schemas"][name]
        new = current["components"]["schemas"][name]
        for property_name, old_property in old.get("properties", {}).items():
            assert property_name in new.get("properties", {})
            assert frozen_schema_shape(
                new["properties"][property_name],
            ) == frozen_schema_shape(
                old_property,
            )
        old_required = set(old.get("required", []))
        new_required = set(new.get("required", []))
        if name in request_names:
            assert new_required - old_required == set()
        else:
            assert new_required == old_required

    for path in (
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/mfa/verify",
        "/api/v1/auth/mfa/setup",
        "/api/v1/auth/mfa/confirm",
    ):
        old_operation = baseline["paths"][path]["post"]
        new_operation = current["paths"][path]["post"]
        assert new_operation["responses"]["200"]["content"][
            "application/json"
        ]["schema"] == old_operation["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
```

Before changing routes, copy the checked-in pre-change
`frontend/openapi.json` byte-for-byte to `backend/openapi_master.json` and
commit it as the mandatory ADR-0003 baseline. Strengthen
`openapi_contract_linter.py` so a missing baseline is a failure (never a
skip), accept explicit baseline/candidate paths, compare existing property
schema shape (`type`, `format`, `$ref`, `items`, `anyOf`/`oneOf`) and reject
newly required request fields in addition to existing
path/method/property-removal checks. It also preserves each existing v1
operation's request schema reference and successful-response schema reference.
Requiredness rules are direction-specific. Request DTOs may relax an existing
required property but may not add a new required property. Frozen response
DTOs (`TokenResponse`, `MFASetupResponse`, `MFAStatusResponse`) must keep the
exact previous `required` set; every added response property is optional.
The linter derives request/response component usage by recursively resolving
`$ref` from each frozen operation's `requestBody` and `responses`; a component
used in both directions receives the stricter response rule. It does not infer
direction from a `Request`/`Response` name suffix.
Unit tests cover removed/renamed/type-changed fields, a new required request
field, a removed required response field and changed `TokenResponse` success
ref as failures. A relaxed required request, optional response field and new
endpoint pass. Also assert that making a formerly optional response property
required fails, so additions stay optional.

The four minimum requiredness unit cases are explicit:

```python
def test_removed_required_response_field_fails():
    baseline, candidate = frozen_pair()
    candidate["components"]["schemas"]["TokenResponse"]["required"].remove(
        "access_token",
    )
    assert "response required set changed" in lint_contract(baseline, candidate)


def test_relaxed_required_request_field_passes():
    baseline, candidate = frozen_pair()
    candidate["components"]["schemas"]["LoginRequest"]["required"].remove(
        "username",
    )
    assert lint_contract(baseline, candidate) == []


def test_new_required_request_field_fails():
    baseline, candidate = frozen_pair()
    candidate["components"]["schemas"]["LoginRequest"]["properties"]["email"] = {
        "type": "string",
    }
    candidate["components"]["schemas"]["LoginRequest"]["required"].append("email")
    assert "new required request field" in lint_contract(baseline, candidate)


def test_optional_response_field_passes():
    baseline, candidate = frozen_pair()
    candidate["components"]["schemas"]["TokenResponse"]["properties"]["next_step"] = {
        "type": "string",
    }
    assert lint_contract(baseline, candidate) == []
```

- [ ] **Step 2: Preserve JWT claim semantics**

New access token claims:

```python
{
    "iss": "identity-v1",
    "aud": "complect-api",
    # Tenant-local compatibility actor, derived from the selected membership.
    "user_id": _pk_to_uuid4(membership.legacy_actor_user_id),
    "legacy_user_pk": membership.legacy_actor_user_id,
    "identity_id": str(identity.identity_id),
    "sub": str(identity.identity_id),
    "tenant_id": str(grant.tenant_id),
    "membership_id": str(grant.membership_id),
    "session_id": str(session.id),
    "grant_id": str(grant.id),
    "identity_security_version": identity.security_version,
    "membership_authz_version": membership.authz_version,
    "tenant_policy_version": authorization_state.policy_version,
    "grant_context_version": grant.context_version,
    "type": "access",
}
```

Do not overwrite `user_id` with `identity_id` or derive it from the canonical
Identity PK. Existing domain/RBAC consumers continue to receive the
deterministic UUID of the selected membership's immutable legacy actor; new
IAM code uses canonical `identity_id`/`sub`. For an unmerged Identity both
rows are the same. Issuer, gateway and backend all use the registered claim
names `iss` and `aud`; no component reads a custom `issuer` claim.

- [ ] **Step 3: Implement typed auth validation failures**

Create:

```python
from ninja.errors import AuthenticationError

from apps.users.services.auth_errors import AuthFailureCode


class AuthContextRejected(AuthenticationError):
    def __init__(self, code: AuthFailureCode):
        self.code = code
        super().__init__(code.value)
```

Reuse Task 6's `IDENTITY_CLAIMS_MISMATCH` and
`TENANT_CONTEXT_MISMATCH` members from the closed 401 authentication group; do
not declare a second enum in the context validator. `config/api.py` registers
`AuthContextRejected` with Task 10's `apps.users.api_errors` adapter, while
ordinary `AuthenticationError` still uses the existing generic 401 handler.

Create `apps/common/contracts/auth_context.py` without importing users:

```python
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AuthenticatedContext:
    identity_id: UUID
    actor_user_id: UUID
    legacy_actor_user_pk: int
    tenant_id: UUID
    membership_id: UUID
    session_id: UUID
    grant_id: UUID
    identity_security_version: int
    membership_authz_version: int
    tenant_policy_version: int
    grant_context_version: int
    permissions: frozenset[str]


class AuthContextValidationProtocol(Protocol):
    def validate(self, token: str) -> AuthenticatedContext: ...
```

`apps.common.api.auth.JWTAuth` owns a one-time
`configure(validator: AuthContextValidationProtocol)` hook. It raises a
startup/configuration error on a second different registration and returns the
closed authentication failure when a request reaches an unconfigured
instance. `apps.users.services.AuthContextValidator` structurally implements
the Protocol and returns only the common DTO. `config/api.py` is the sole
composition root allowed to import both layers and calls
`jwt_auth.configure(AuthContextValidator())`. Modify `config/urls.py` so it imports `config.api`
before importing routers whose module-level `auth=jwt_auth` references the
singleton; this avoids a partially configured import cycle.

`apps/common/tests/test_auth_context_boundary.py` asserts that common auth and
contract files contain no `apps.users` import. Fake/unconfigured unit cases
instantiate a fresh `JWTAuth()` and never reconfigure module-level `jwt_auth`;
the real singleton may already be configured during pytest collection through
`config.urls`, so its test is read-only and asserts the registered validator is
Protocol-conforming. There is no test-only reset hook on the production
singleton and test order cannot change registration. Run
`infra/scripts/ddd_boundary_linter.py` in this task.

`AuthContextValidator` checks signature, `iss`, `aud`, expiry and UUID claim
syntax first. Before any permissions or version decision it loads the
canonical Identity from primary and enforces:

```python
if claims["sub"] != claims["identity_id"]:
    raise AuthContextRejected(AuthFailureCode.IDENTITY_CLAIMS_MISMATCH)

identity = User.objects.using("default").get(
    identity_id=claims["identity_id"],
)
```

Under the signed tenant RLS context it then performs primary, read-only loads
of the exact session, membership, grant and `TenantAuthorizationState` and
enforces:

```python
if membership.identity_id != identity.identity_id:
    raise AuthContextRejected(AuthFailureCode.IDENTITY_CLAIMS_MISMATCH)
if (
    membership.legacy_actor_user_id != claims["legacy_user_pk"]
    or uuid.UUID(_pk_to_uuid4(membership.legacy_actor_user_id))
    != uuid.UUID(claims["user_id"])
):
    raise AuthContextRejected(AuthFailureCode.IDENTITY_CLAIMS_MISMATCH)
if (
    grant.session_id != uuid.UUID(claims["session_id"])
    or grant.membership_id != uuid.UUID(claims["membership_id"])
    or grant.tenant_id != uuid.UUID(claims["tenant_id"])
):
    raise AuthContextRejected(AuthFailureCode.TENANT_CONTEXT_MISMATCH)
```

Only after those bindings match does it evaluate Identity version/status,
`session.security_version_snapshot`, session revoke/expiry, membership
status/version, tenant policy version and grant state/context. A superseded
grant raises `TENANT_SWITCHED`.
The access-JWT validator never uses `SELECT FOR UPDATE`; parallel protected
requests from one browser session must not serialize on auth rows. Row locks
are reserved for refresh, tenant switch, revoke, recovery and role/status
mutations.
The actor row is loaded/proved through the membership FK only as an immutable
compatibility subject. A merged source tombstone's password, `is_active` and
security fields are never credentials and never override the canonical
Identity decision. The returned `AuthenticatedContext` carries both canonical
`identity_id` and tenant-local `actor_user_id`/`legacy_actor_user_pk` so
callers cannot silently confuse them; the canonical Django PK is an internal
users-domain implementation detail.
The typed users handler preserves `exc.code`, exact policy status and optional
`Retry-After`; it never collapses a typed context error into
`AUTHENTICATION_REQUIRED`.

- [ ] **Step 4: Update schemas and routes**

Define `ErrorEnvelope` once in `apps.common.api.errors` and use it both in the
generic builder and route schemas. In `apps.users.api`, declare named,
route-local maps instead of one over-broad fallback map, for example:

```python
LOGIN_RESPONSES = {
    200: TokenResponse,
    401: ErrorEnvelope,
    422: ErrorEnvelope,
    429: ErrorEnvelope,
    503: ErrorEnvelope,
}
TENANT_SELECT_RESPONSES = {
    200: TokenResponse,
    401: ErrorEnvelope,
    403: ErrorEnvelope,
    409: ErrorEnvelope,
    503: ErrorEnvelope,
}
REFRESH_RESPONSES = {
    200: TokenResponse,
    401: ErrorEnvelope,
    403: ErrorEnvelope,
    503: ErrorEnvelope,
}
MFA_VERIFY_RESPONSES = {
    200: TokenResponse,
    401: ErrorEnvelope,
    429: ErrorEnvelope,
    503: ErrorEnvelope,
}
MFA_SETUP_RESPONSES = {
    200: MFASetupResponse,
    401: ErrorEnvelope,
    403: ErrorEnvelope,
    409: ErrorEnvelope,
    503: ErrorEnvelope,
}
MFA_CONFIRM_RESPONSES = {
    200: MFAStatusResponse,
    400: ErrorEnvelope,  # frozen v1 response remains declared
    401: ErrorEnvelope,
    409: ErrorEnvelope,
    429: ErrorEnvelope,
    503: ErrorEnvelope,
}
RECOVERY_START_RESPONSES = {
    202: RecoveryAcceptedResponse,
    503: ErrorEnvelope,
}


@router.post("/login", response=LOGIN_RESPONSES, auth=None)
def login(request, body, response): ...


@router.post("/tenant/select", response=TENANT_SELECT_RESPONSES, auth=None)
def tenant_select(request, body, response): ...
```

Declare corresponding exact maps for MFA setup/confirm/verify, logout,
logout-all, password change, session list/delete and both recovery-complete
routes. Include `429` only where threshold exhaustion is returned to the
caller, `409` only for reachable idempotency/state conflicts, and `503` where
an unavailable dependency or reconciliation failure can escape. Recovery
start deliberately suppresses threshold `429` to its generic `202`, but still
declares limiter/dependency `503`. The generated OpenAPI assertions are the
contract that keeps these maps aligned with Task 6's exhaustive runtime
policy.

Preserve the existing DTO names, fields and property types exactly; add only
optional fields for the new state machine:

```python
import uuid
from datetime import datetime
from typing import Literal

from ninja import Schema
from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError


class MembershipChoice(Schema):
    membership_id: uuid.UUID
    tenant_id: uuid.UUID
    display_name: str


class LoginRequest(Schema):
    # Existing string property remains. Making it optional via a string
    # default is a relaxation; its OpenAPI type is not changed to nullable.
    username: str = Field(default="", max_length=150)
    email: str | None = Field(default=None, min_length=1, max_length=254)
    password: str = Field(min_length=1)
    device_fingerprint: str = Field(default="", max_length=128)

    @model_validator(mode="after")
    def validate_identity_input(self):
        username_present = bool(self.username)
        email_present = self.email is not None
        if not username_present and not email_present:
            raise PydanticCustomError(
                "IDENTITY_INPUT_REQUIRED",
                "username or email is required",
            )
        if username_present and email_present:
            raise PydanticCustomError(
                "AMBIGUOUS_IDENTITY_INPUT",
                "pass exactly one identity field",
            )
        return self


class MFAVerifyRequest(Schema):
    # Frozen v1 field name is retained. The service accepts either a six-digit
    # TOTP or a backup factor; widening maxLength is non-destructive.
    totp_code: str = Field(min_length=6, max_length=128)


class TokenResponse(Schema):
    # These four fields are the frozen v1 contract.
    access_token: str
    token_type: str = "bearer"
    mfa_required: bool = False
    permissions: list[str] = Field(default_factory=list)

    # IAM-IDENTITY additions are optional only.
    next_step: Literal[
        "mfa",
        "mfa_setup",
        "tenant_selection",
        "authenticated",
    ] | None = None
    memberships: list[MembershipChoice] | None = None
    active_tenant_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    expires_at: datetime | None = None


class RefreshRequest(Schema):
    refresh_token: str


class MFASetupResponse(Schema):
    secret: str
    otpauth_url: str


class MFAStatusResponse(Schema):
    mfa_enabled: bool
    backup_codes: list[str] | None = None
    post_mfa: TokenResponse | None = None


class RecoveryStartRequest(Schema):
    email: str = Field(min_length=1, max_length=254)


class PasswordRecoveryCompleteRequest(Schema):
    selector: str = Field(min_length=1, max_length=2048)
    verifier: str = Field(min_length=32, max_length=512)
    new_password: str = Field(min_length=12, max_length=128)


class MfaRecoveryCompleteRequest(Schema):
    selector: str = Field(min_length=1, max_length=2048)
    verifier: str = Field(min_length=32, max_length=512)


class PasswordChangeRequest(Schema):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)
```

The schema preserves Pydantic's frozen-v1 `extra="ignore"` behavior; adding
`extra="forbid"` would be a breaking request-contract change. In particular,
an extra `tenant_id` is ignored and never becomes an authorization input, and
the API test proves the resulting partial token contains no tenant claim.
The schema preserves `username` as a non-null string property but relaxes its
requiredness so email-only clients can use the new optional field. It rejects
both identifiers being present as typed
`AMBIGUOUS_IDENTITY_INPUT` before either value reaches a repository. With
exactly one field, `username` keeps exact legacy username semantics and
`email` uses Task 2 normalization. Unknown username/email and wrong password
all return the same generic invalid-credentials result. No dual-identifier
branch performs lookup-dependent classification. The users-domain validation
adapter preserves the two closed custom validation codes in `ErrorEnvelope`;
it does not perform an Identity query.

Map internal results to the frozen response DTO as follows:

- password step: `access_token=<partial>`, `mfa_required=True`, empty
  permissions and `next_step=mfa|mfa_setup`;
- multi-membership post-MFA: `access_token=<TENANT_SELECTION>`,
  `mfa_required=True`, empty permissions, `next_step=tenant_selection` and
  optional membership choices;
- authenticated: `access_token=<access JWT>`, `mfa_required=False`, current
  permissions and `next_step=authenticated`, with optional tenant/session IDs;
- refresh continues to return the same required `TokenResponse` fields.

Thus a legacy client always receives the required `access_token` field it
already understands, but partial/selection token types remain unusable on
protected routes. New clients branch on optional `next_step`.

`/mfa/setup` and `/mfa/confirm` are context-free routes authenticated only by a
bearer `MFA_SETUP_PARTIAL`; they do not require or accept an access JWT.
Confirmation displays the ten codes and then feeds its `TENANT_SELECTION`
result through the same Task 8 resolver: one enabled membership returns the
authenticated branch, multiple return the selection branch. No session exists
before successful confirmation. Its HTTP body remains
`MFAStatusResponse(mfa_enabled=True)` and carries `backup_codes` plus
`post_mfa: TokenResponse` only as additive optional fields.

The only canonical selection route is
`POST /api/v1/auth/tenant/select` (no trailing slash). It is registered with
`auth=None`; neither Ninja's generic access auth nor access middleware runs
first. Task 10's exact Kong route preserves raw `Authorization`, strips every
identity/tenant context header and forwards only gateway trust/client-IP
headers. The endpoint extracts the bearer bytes without decoding and passes
them first to Task 8's dedicated selection decoder. This ordering lets the
decoder distinguish strict live validation from the narrow existing-result-
only expired replay without disabling `exp` globally. Every other method/path
shape is rejected by its route-specific token policy.

Tenant select requires UUID `Idempotency-Key`, canonicalizes the authenticated
flow/target/current-context into Task 8's request fingerprint, and maps it to
`TenantGrantSwitchOperation.operation_id`. Concurrent HTTP requests and a
retry after a lost response return byte-identical JSON, refresh cookie and
CSRF cookie from the encrypted result envelope. A reused key with a different
fingerprint returns `409 IDEMPOTENCY_KEY_REUSED`; an otherwise valid replay
between the selection token's five-minute expiry and the result's ten-minute
expiry is routed through Task 8's existing-result-only decoder. An unknown or
pending operation still returns `401 TENANT_SELECTION_EXPIRED`. A replay after
its ten-minute result window returns
`409 IDEMPOTENCY_RESULT_EXPIRED` and never rotates again. Cookie-auth switch
also requires Task 10 CSRF guard. Individual session delete filters by
authenticated `identity_id`.

Both recovery start routes call
`start_recovery(email, PASSWORD_RESET|MFA_RESET)` and unconditionally return
`202 {"status": "accepted"}` after the rate-limit decision. Complete routes do
not reveal whether selector, purpose or verifier failed; they return the typed
generic recovery error and never echo any token material. Successful MFA
completion returns `204`, clears auth cookies and requires the next flow to
begin at password login.

`/password/change` requires a validated access context, calls Task 9
`change_password()` with the authenticated `identity_id`, returns `204`, and
clears refresh/CSRF cookies because `security_version` invalidates every
session. Wrong current password uses the generic invalid-credentials envelope;
the route cannot target another Identity.

Preserve request-body refresh tokens behind
`IAM_ALLOW_REFRESH_BODY_COMPAT=True` for the lifetime of `/api/v1`; production
v1 keeps the setting true and the cookie takes precedence. When the body
fallback is used, increment
`iam_refresh_token_body_compat_total` and emit non-secret structured telemetry
containing request/correlation IDs only. The false-setting branch remains a
tested emergency fail-closed control but is not a v1 retirement mechanism.
Never log or label a metric with the refresh token, identity or email. A future
cookie-only `/api/v2` must carry its own ADR, consumer migration and frozen
contract baseline.

- [ ] **Step 5: Update browser state machine**

Frontend store states are
`anonymous | mfa_setup | mfa | tenant_selection | authenticated`. Store access
token only in memory. While `next_step` is not `authenticated`, treat the
frozen `TokenResponse.access_token` field as an opaque partial/selection token,
never as a protected access token, and keep it only in memory.
After setup confirmation, require the user to acknowledge/download the ten
backup codes before advancing to the returned post-MFA branch.
`BroadcastChannel("complect-auth")` publishes `TENANT_SWITCHED` and
`SESSION_REVOKED`, while backend errors remain authoritative.

The login page exposes password recovery without revealing account existence.
Delivery links open the purpose-specific recovery pages with selector/verifier
in the URL fragment, not query parameters; the page removes the fragment with
`history.replaceState` immediately after reading it. Password completion asks
for the new password. MFA completion shows that all sessions were closed and
routes to a fresh password login followed by MFA setup. Selector/verifier are
kept only in component memory and never sent to analytics.

- [ ] **Step 6: Run API, OpenAPI and frontend tests**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_identity_auth_api.py apps/users/tests/test_identity_auth_openapi.py apps/users/tests/test_auth_service.py apps/users/tests/test_deferred_merge_entrypoint_coverage.py apps/common/tests/test_error_envelope.py apps/common/tests/test_auth_context_boundary.py -v
docker compose -f docker-compose.dev.yml run --rm --no-deps -v "$PWD:/workspace:ro" -w /workspace --entrypoint python backend infra/scripts/ddd_boundary_linter.py
docker compose -f docker-compose.dev.yml exec -T backend python manage.py export_openapi > /tmp/iam-identity-openapi.json
python3 infra/scripts/openapi_contract_linter.py --baseline backend/openapi_master.json --candidate /tmp/iam-identity-openapi.json
python3 -m unittest infra.scripts.tests.test_openapi_contract_linter -v
make generate-sdk
make tsc
docker compose -f docker-compose.dev.yml exec frontend npx vitest run src/shared/auth/store.test.ts
```

Expected: PASS; the frozen v1 linter runs before SDK generation and generated
SDK is changed only through `make generate-sdk`.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/users/api.py backend/apps/users/schemas/auth.py backend/apps/users/services/auth_context_validator.py backend/apps/users/observability.py backend/apps/users/api_errors.py backend/apps/users/security/credential_entrypoints.py backend/apps/users/security/credential_entrypoint_manifest.json backend/apps/common/api/auth.py backend/apps/common/api/errors.py backend/apps/common/contracts/auth_context.py backend/apps/common/tests/test_auth_context_boundary.py backend/config/api.py backend/config/urls.py backend/config/settings/base.py backend/openapi_master.json infra/scripts/openapi_contract_linter.py infra/scripts/tests/test_openapi_contract_linter.py backend/apps/users/tests/test_identity_auth_api.py backend/apps/users/tests/test_identity_auth_openapi.py backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py frontend/src/shared/auth frontend/src/app/'(auth)'/login frontend/src/shared/api/generated backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py
git commit -m "feat(iam): expose mandatory mfa identity auth flow"
```

---

### Task 13: Dual-Read Shadow Comparison and Atomic Feature Mode

**Files:**

- Create: `backend/apps/users/services/auth_rollout.py`
- Modify: `backend/apps/users/services/auth_service.py`
- Modify: `backend/apps/common/api/auth.py`
- Modify: `backend/config/settings/base.py`
- Modify: `backend/config/settings/local.py`
- Modify: `backend/config/settings/prod.py`
- Modify: `backend/apps/users/security/credential_entrypoints.py`
- Modify: `backend/apps/users/security/credential_entrypoint_manifest.json`
- Create: `backend/apps/users/management/commands/revoke_identity_v1_sessions.py`
- Test: `backend/apps/users/tests/test_auth_rollout.py`
- Test: `backend/apps/users/tests/test_revoke_identity_v1_sessions.py`
- Test: `backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py`

**Interfaces:**

- Produces `IAM_IDENTITY_AUTH_MODE = LEGACY | SHADOW | ENFORCE`.
- `LEGACY`: complete legacy issuer/validator only; identity session endpoints closed.
- `SHADOW`: legacy decides; new path performs read-only comparison and records metrics only.
- `ENFORCE`: new issuer and new validator decide together; legacy fallback is
  never used. Through Task 14 only, the old path may still run as a bounded
  read-only comparator that emits paired evidence but cannot change, delay or
  rescue the new decision.
- Produces idempotent `revoke_identity_v1_sessions --batch-size 5000
  --reason rollback` using the session repository and primary DB only.
- Replaces issuer/validator dispatch and rollback-command entries in the
  credential entry-point manifest. Mode activation requires static coverage
  plus a fresh exact-build attestation; Task 12 evidence is not reusable.

- [ ] **Step 1: Write failing rollout tests**

```python
@override_settings(IAM_IDENTITY_AUTH_MODE="SHADOW")
@pytest.mark.django_db(transaction=True)
def test_shadow_mismatch_does_not_authorize_or_deny_differently(legacy_login_request):
    result = auth_service.login(**legacy_login_request)
    assert result.source == "legacy"
    record = IdentitySecurityAuditRecord.objects.get(event_type="auth.identity_shadow_compared")
    assert record.metadata["matched"] is False
    assert record.metadata["mismatch_code"] == "CLAIM_CONTEXT_MISMATCH"


@override_settings(IAM_IDENTITY_AUTH_MODE="LEGACY")
def test_legacy_mode_rejects_identity_session_endpoints(client):
    response = client.get("/api/v1/auth/sessions")
    assert response.status_code == 404


@override_settings(IAM_IDENTITY_AUTH_MODE="ENFORCE")
def test_new_token_version_failure_never_falls_back_to_legacy(identity_access_token):
    with pytest.raises(AuthContextRejected, match="AUTHZ_VERSION_STALE"):
        validate_stale_token(identity_access_token)


@pytest.mark.django_db(transaction=True)
def test_shadow_attempt_has_one_durable_comparison(legacy_login_request):
    auth_service.login(**legacy_login_request)
    eligible = IdentitySecurityAuditRecord.objects.get(
        event_type="auth.identity_shadow_eligible",
    )
    compared = IdentitySecurityAuditRecord.objects.get(
        event_type="auth.identity_shadow_compared",
        correlation_id=eligible.correlation_id,
    )
    assert compared.metadata["eligible_event_id"] == str(eligible.id)
```

- [ ] **Step 2: Implement one atomic mode switch**

`auth_rollout.py` is the only code that selects issuer/validator. There are no separate production flags that could enable the issuer without its validator.

```python
class IdentityAuthMode(StrEnum):
    LEGACY = "LEGACY"
    SHADOW = "SHADOW"
    ENFORCE = "ENFORCE"
```

Every shadow-eligible auth attempt first writes
`auth.identity_shadow_eligible`. Its completion writes exactly one
`auth.identity_shadow_compared` to the append-only global audit table with the
eligible record ID, pseudonymized identity ID, legacy/new decision categories,
`matched: bool` and a typed mismatch code. It never logs tokens, email or MFA
data. Task 14 calculates seven-day coverage from these paired durable records
rather than process-local metrics or an arbitrary high-volume threshold.
The comparison hook runs in both `SHADOW` and the bounded Task 14 `ENFORCE`
stabilization window: `SHADOW` serves the legacy decision, while `ENFORCE`
serves the new decision before comparison and never falls back. Comparator
failure cannot alter the request result, but missing durable pairing blocks
the release gate. One production-safe daily canary writes a typed canary
eligible/comparison pair so each of the seven UTC days has non-vacuous
evidence; organic traffic volume is not a threshold.

Keep `IAM_ALLOW_REFRESH_BODY_COMPAT=True` for `/api/v1`. The token-free metric
remains useful input to a future `/api/v2` migration, but a zero interval does
not authorize changing the frozen v1 request or runtime behavior.

- [ ] **Step 3: Implement safe rollback**

Rollback procedure:

1. set mode to `LEGACY`;
2. close session/grant/tenant-select endpoints;
3. reject tokens carrying `iss="identity-v1"` in the legacy validator;
4. run `python manage.py revoke_identity_v1_sessions --reason rollback` to
   revoke all still-open rows in `IdentitySession` (the table contains only
   identity-v1 session families);
5. continue serving only complete legacy auth.

Never use `LOG_ONLY` for identity security versions. If safe legacy rollback
is no longer possible after contract retirement, remain `ENFORCE` and fail
closed.

The command reads distinct candidate Identity UUIDs from primary without
locking target sessions, sorts them by UUID bytes and calls Task 5's
merge-aware `revoke_all_sessions()` under the common coordinator. Advisory
contention is counted/rescheduled; it never locks an `IdentitySession` before
its User/merge graph and never uses target `skip_locked`. It emits counts only
and is idempotent.

Create `backend/apps/users/tests/test_revoke_identity_v1_sessions.py`:

```python
@pytest.mark.django_db(transaction=True)
def test_command_revokes_every_open_identity_session(identity_sessions):
    call_command(
        "revoke_identity_v1_sessions",
        "--batch-size",
        "2",
        "--reason",
        "rollback",
    )
    assert not IdentitySession.objects.filter(revoked_at__isnull=True).exists()

    call_command("revoke_identity_v1_sessions", "--reason", "rollback")
    assert not IdentitySession.objects.filter(revoked_at__isnull=True).exists()
```

- [ ] **Step 4: Run rollout tests**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_auth_rollout.py apps/users/tests/test_revoke_identity_v1_sessions.py apps/users/tests/test_identity_auth_api.py apps/users/tests/test_deferred_merge_entrypoint_coverage.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/users/services/auth_rollout.py backend/apps/users/services/auth_service.py backend/apps/common/api/auth.py backend/config/settings/base.py backend/config/settings/local.py backend/config/settings/prod.py backend/apps/users/security/credential_entrypoints.py backend/apps/users/security/credential_entrypoint_manifest.json backend/apps/users/management/commands/revoke_identity_v1_sessions.py backend/apps/users/tests/test_auth_rollout.py backend/apps/users/tests/test_revoke_identity_v1_sessions.py backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py
git commit -m "feat(iam): add atomic identity auth rollout"
```

---

### Task 14: Stabilization and Contract Release Gate

**Files:**

- Create: `backend/apps/users/management/commands/check_identity_stabilization.py`
- Create: `backend/apps/users/tests/test_identity_stabilization_gate.py`
- Modify: `backend/apps/users/infrastructure/iam_tenant_inventory.py`
- Create: `docs/runbooks/iam-identity-release-gates.md`
- Modify: `docs/adr/0024-global-identity-tenant-membership.md`
- Modify: `docs/superpowers/plans/2026-07-30-iam-identity.md`

**Interfaces:**

- Produces `check_identity_stabilization --window-days 7` for exact durable DB
  invariants only.
- Produces a separate release runbook for gateway runtime, limiter metrics,
  OpenAPI/SDK, browser acceptance and migration-lint disposition.
- Consumes the exact Task 4 `DurableIamTenantInventoryProvider`; it does not
  discover tenant-scoped rows through an unscoped ORM query or legacy column.

- [ ] **Step 1: Write failing gate tests**

```python
import pytest
from django.core.management import CommandError, call_command

from apps.users.models import IdentitySecurityAuditRecord


@pytest.mark.django_db(transaction=True)
def test_gate_fails_on_unresolved_reconciliation(
    unresolved_refresh_reconciliation,
):
    assert unresolved_refresh_reconciliation.resolved_at is None
    with pytest.raises(CommandError, match="unresolved_reconciliation"):
        call_command("check_identity_stabilization", "--window-days", "7")


@pytest.mark.django_db(transaction=True)
def test_gate_fails_when_enabled_identity_has_no_confirmed_mfa(identity):
    type(identity).objects.filter(pk=identity.pk).update(
        security_status=type(identity).SecurityStatus.ACTIVE,
    )
    with pytest.raises(CommandError, match="identities_without_mfa"):
        call_command("check_identity_stabilization", "--window-days", "7")


@pytest.mark.django_db(transaction=True)
def test_gate_fails_when_shadow_eligible_record_has_no_comparison(
    shadow_eligible_record,
):
    with pytest.raises(CommandError, match="shadow_comparisons_missing"):
        call_command("check_identity_stabilization", "--window-days", "7")


@pytest.mark.django_db(transaction=True)
def test_gate_fails_on_any_shadow_mismatch(shadow_mismatch_pair):
    # The factory inserts immutable evidence with matched=False initially;
    # tests never rewrite an existing audit record.
    assert shadow_mismatch_pair.compared.metadata["matched"] is False
    with pytest.raises(CommandError, match="shadow_mismatches"):
        call_command("check_identity_stabilization", "--window-days", "7")


@pytest.mark.django_db(transaction=True)
def test_gate_fails_on_pending_or_invalid_deferred_merge(
    pending_merge_with_distinct_credentials,
):
    assert (
        pending_merge_with_distinct_credentials.operation.state
        == IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE
    )
    with pytest.raises(CommandError, match="pending_merge_convergences"):
        call_command("check_identity_stabilization", "--window-days", "7")


@pytest.mark.django_db(transaction=True)
def test_gate_requires_current_runtime_merge_coverage_when_aliases_exist(
    valid_deferred_merge_graph,
    expired_runtime_merge_coverage_attestation,
):
    with pytest.raises(CommandError, match="merge_coverage_attestation"):
        call_command("check_identity_stabilization", "--window-days", "7")


@pytest.mark.django_db(transaction=True)
def test_gate_rejects_vacuous_window_without_one_canary_pair_per_day(
    six_of_seven_daily_shadow_canary_pairs,
):
    with pytest.raises(CommandError, match="shadow_canary_days_missing"):
        call_command("check_identity_stabilization", "--window-days", "7")
```

- [ ] **Step 2: Implement exact acceptance queries**

The command loads the configured Task 4 durable registry inventory once, enters
each yielded tenant context separately and exits non-zero unless:

- identity/null/duplicate/orphan counts from Task 4 are zero;
- every `ACTIVE` Identity has a confirmed MFA factor, every
  `MFA_SETUP_REQUIRED` Identity has no confirmed factor, and no other
  status/factor contradiction exists;
- every inventoried tenant has exactly one `TenantAuthorizationState` and no
  membership whose Identity is missing;
- no unresolved `SessionReconciliationRecord`;
- no reconciliation row violates its kind/switch-session/proof schema;
- no orphan or rebound recovery operation/token pair and no invalid
  consumed/revoked or delivery-state shape;
- no `TenantGrantSwitchOperation` remains non-terminal for more than five minutes;
- no session has more than one open grant across the aggregate of all
  inventoried tenant contexts;
- no grant violates membership tenant or session identity integrity;
- no `APPROVED_PENDING_CONVERGENCE` operation and no invalid/incomplete
  relational proof for an `APPROVED_DEFERRED`, `READY_TO_FINALIZE` or
  `FINALIZED` operation;
- when any deferred source alias exists, the deployed Task 2 guard is active
  and the exact-build credential-entry-point coverage attestation is valid,
  unexpired and matches the runtime manifest; a boolean flag alone is not
  accepted;
- every `auth.identity_shadow_eligible` audit row in the last seven consecutive
  days has exactly one `auth.identity_shadow_compared` row referencing its ID;
- no paired shadow comparison has `matched=False`.
- each of the seven complete UTC days contains at least one production-safe
  typed canary eligible/comparison pair, so an empty organic window cannot pass
  vacuously; canary payloads remain pseudonymized and use no bypass account.

All checks are exact primary-DB queries. Run it only after the bounded
`ENFORCE` comparator has produced seven complete daily canary/pairing windows;
the new decision remains authoritative throughout. The command does not claim that
Prometheus, Kong, OpenAPI generation, browser acceptance or migration lint
ran, and it has no `--minimum-shadow-decisions` option. Low traffic is handled
by complete eligible/comparison pairing plus the external daily canary, not a
10,000-decision threshold.

The command accumulates only non-secret `(tenant_id, session_id, count)` and
integrity counters while moving through RLS contexts. It never carries a
tenant-scoped model instance into another tenant context. Tests use an
inventory with tenants A and B and assert both contexts were entered. A pure
aggregation unit test feeds one synthetic scoped count for the same session
from A and B and proves the aggregate reports two; the integration test does
not disable the production unique constraint to manufacture corrupt rows.
Task 4 preflight, this stabilization command and Task 17's N+3b gate are tested
against the same provider class.

- [ ] **Step 3: Run the database stabilization gate**

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_identity_stabilization_gate.py -v
docker compose -f docker-compose.dev.yml exec backend python manage.py reconcile_identity_sessions --batch-size 500
docker compose -f docker-compose.dev.yml exec backend python manage.py preflight_identity_contract
docker compose -f docker-compose.dev.yml exec backend python manage.py check_identity_stabilization --window-days 7
```

Expected: the command reports only named DB counts and exits zero when they are
all zero.

- [ ] **Step 4: Write and execute the external release runbook**

Create `docs/runbooks/iam-identity-release-gates.md` with these separately
recorded commands and evidence sources:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests -v
make test-common
make lint
make types
docker compose -f docker-compose.dev.yml run --rm --no-deps -v "$PWD:/workspace:ro" -w /workspace --entrypoint python backend infra/scripts/migration_linter.py
make generate-sdk
make tsc
docker compose -f docker-compose.dev.yml exec backend python manage.py check --deploy
make agent-infra-test
git diff --check
```

The runbook also requires:

```promql
sum(increase(iam_shadow_eligible_total[7d]))
sum(increase(iam_shadow_compared_total[7d]))
sum(increase(iam_shadow_mismatch_total[7d]))
sum(increase(iam_refresh_token_body_compat_total[7d]))
sum by (flow) (increase(iam_auth_rate_limit_decisions_total[7d]))
```

For the soak window: eligible equals compared, mismatch is zero, and the DB
pairing query from Step 3 is zero-missing/zero-mismatch. Run one production-safe
login/refresh canary daily so a low-traffic deployment still exercises the
path. Report refresh-body compatibility usage as advisory migration evidence;
do not disable it in v1. Limiter metrics must contain `login`, `mfa` and `recovery` series; no
metric label contains identity, email, token or raw IP.

Expected status:

- IAM-specific checks: `PASS`;
- repository migration lint: `KNOWN FAILURE —
  backend/apps/requests/migrations/0002_add_resource_types.py`;
- merge readiness: `BLOCKED` until the Requests owner fixes that migration or
  grants a formal, time-bounded waiver recorded in the release decision.

Do not edit `requests/*`, suppress that path globally or change the linter to
hide the failure. Any other failure is recorded with its exact command and
cause; the IAM plan is not stabilized until it is classified and every
IAM-specific gate passes.

- [ ] **Step 5: Perform browser acceptance**

Through Kong on port 8000 verify:

1. an Identity with a factor receives the MFA step and no refresh cookie;
2. an Identity without a factor completes setup, receives ten backup codes and
   has no session before confirmation;
3. wrong MFA is rate-limited per challenge for ten minutes and creates no
   session;
4. one membership auto-selects after MFA;
5. multiple memberships show tenant selection;
6. A→B invalidates the old tab with `TENANT_SWITCHED`;
7. concurrent/retried A→B with one Idempotency-Key returns the exact same
   access token, refresh cookie and CSRF cookie, including after simulated
   loss of the first response;
8. refresh rotates cookie and reuse revokes the family;
9. individual session revoke affects only the selected session;
10. logout-all revokes all sessions;
11. known/unknown password and MFA recovery start responses are
    indistinguishable, delivery links complete without exposing secrets in
    API/log/outbox payloads, and MFA recovery forces fresh setup;
12. abandoned MFA setup expires and a fresh password login can enroll and
    confirm a new TOTP secret;
13. spoofed gateway context and client-IP headers are ignored/replaced;
14. v1 body-only refresh remains accepted with token-free compatibility
    telemetry while cookie transport takes precedence; the false-flag branch
    is tested only as an emergency fail-closed control.

- [ ] **Step 6: Record release decision**

Append the actual production gate evidence and approval reference to the release runbook outside this implementation plan. Do not put email collision artifacts, token values, secrets or PII in git.

- [ ] **Step 7: Commit stabilization tooling/document sync**

```bash
git add backend/apps/users/management/commands/check_identity_stabilization.py backend/apps/users/infrastructure/iam_tenant_inventory.py backend/apps/users/tests/test_identity_stabilization_gate.py docs/runbooks/iam-identity-release-gates.md docs/adr/0024-global-identity-tenant-membership.md docs/superpowers/plans/2026-07-30-iam-identity.md backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py
git commit -m "feat(iam): add identity stabilization gate"
```

---

### Task 15: Release N+3a Membership-Backed Compatibility (No DDL)

**Files:**

- Modify: `backend/apps/users/services/auth_service.py`
- Modify: `backend/apps/users/services/mfa_service.py`
- Modify: `backend/apps/users/services/user_admin_service.py`
- Modify: `backend/apps/users/services/deferred_merge_guard.py`
- Modify: `backend/apps/users/queries/user_queries.py`
- Modify: `backend/apps/users/contracts/user_info_impl.py`
- Modify: `backend/apps/users/services/pii_access_audit_service.py`
- Modify: `backend/apps/users/schemas/auth.py`
- Modify: `backend/apps/users/api.py`
- Modify: `backend/apps/users/observability.py`
- Modify: `backend/apps/users/security/credential_entrypoints.py`
- Modify: `backend/apps/users/security/credential_entrypoint_manifest.json`
- Test: `backend/apps/users/tests/test_membership_backed_user_compatibility.py`
- Test: `backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py`
- Modify: `backend/apps/users/tests/conftest.py`
- Modify: `backend/apps/users/tests/iam_factories.py`

**Interfaces:**

- Produces membership-backed compatibility list/admin/user-info paths with no
  `User.tenant_id` read or write. The frozen v1 integer `items[].id` and every
  integer path `user_id` are exactly
  `TenantMembership.legacy_actor_user_id`; `user_uuid` is
  `_pk_to_uuid4(membership.legacy_actor_user_id)`. The canonical Identity
  integer PK is never serialized or accepted as a tenant-local actor ID.
- Email-bearing list/user-info reads use the Task 2 audit-before-PII service
  and fail closed before the global Identity query when audit is unavailable.
- Moves server-side tenant, MFA and device authority to
  `TenantMembership`, `MfaCredential`, `PreAuthChallenge` and
  `IdentitySession.device_metadata`, while leaving every legacy database
  column physically present and instrumented for the Task 16 soak.
- Retains the production legacy issuer/validator, temporary
  `legacy_user_pk`/`username` access claims, `user.*` event aliases and the
  deprecated frozen-v1 refresh request body. Task 15 performs no DDL, no
  refresh-key purge and no destructive cleanup.
- Keeps `users.User.password`, integer PK, stable `identity_id` and the
  immutable membership-to-actor mapping. Global credentials resolve through
  the canonical Identity; domain/RBAC compatibility continues through the
  membership actor.
- Requires the shared dormant activation guard before tenant membership
  enable or any compatibility path can promote the canonical Identity. A
  `DORMANT_FAIL_CLOSED` graph in deferred/ready state returns
  `MERGE_DORMANT_FINALIZATION_REQUIRED`; no membership/version/session field
  changes. The moved enable/disable adapters replace their old manifest paths
  and require a fresh exact-build coverage attestation in this release.

- [ ] **Step 1: Write failing actor-compatibility and tenant-isolation tests**

```python
import uuid

import pytest

from apps.common.tenancy.context import clear_tenant_context, set_tenant_context
from apps.users.contracts.user_info_impl import DjangoUserInfoProvider
from apps.users.models import TenantMembership, User
from apps.users.queries.user_queries import list_users
from apps.users.services.auth_errors import AuthFailureCode, AuthFlowError
from apps.users.services.auth_service import _pk_to_uuid4
from apps.users.services.user_admin_service import disable_user


@pytest.mark.django_db(transaction=True)
def test_user_listing_is_membership_backed_and_tenant_isolated(
    identity_with_two_memberships,
    pii_read_context,
):
    identity = identity_with_two_memberships.identity
    tenant_a = identity_with_two_memberships.tenant_a
    tenant_b = identity_with_two_memberships.tenant_b
    membership_a = identity_with_two_memberships.membership_a
    membership_b = identity_with_two_memberships.membership_b

    [item_a] = list_users(
        tenant_a,
        pii_context=pii_read_context.for_tenant(tenant_a),
    )
    [item_b] = list_users(
        tenant_b,
        pii_context=pii_read_context.for_tenant(tenant_b),
    )
    assert item_a.id == membership_a.legacy_actor_user_id
    assert item_a.user_uuid == _pk_to_uuid4(membership_a.legacy_actor_user_id)
    assert item_b.id == membership_b.legacy_actor_user_id
    assert item_b.user_uuid == _pk_to_uuid4(membership_b.legacy_actor_user_id)

    set_tenant_context(tenant_b)
    try:
        TenantMembership.objects.filter(
            identity_id=identity.identity_id,
        ).update(status=TenantMembership.Status.DISABLED)
    finally:
        clear_tenant_context()

    assert [
        item.id
        for item in list_users(
            tenant_a,
            pii_context=pii_read_context.for_tenant(tenant_a),
        )
    ] == [membership_a.legacy_actor_user_id]
    assert list_users(
        tenant_b,
        pii_context=pii_read_context.for_tenant(tenant_b),
    )[0].is_active is False


@pytest.mark.django_db(transaction=True)
def test_user_listing_does_not_read_email_when_pii_audit_fails(
    identity_with_two_memberships,
    pii_read_context,
    mocker,
):
    tenant_id = identity_with_two_memberships.tenant_a
    global_pii_query = mocker.patch(
        "apps.users.queries.user_queries.load_identity_email_projection",
    )
    mocker.patch(
        "apps.users.queries.user_queries."
        "PiiAccessAuditService.append_batch_and_commit",
        side_effect=RuntimeError("audit unavailable"),
    )

    with pytest.raises(AuthFlowError) as caught:
        list_users(
            tenant_id,
            pii_context=pii_read_context.for_tenant(tenant_id),
        )

    assert caught.value.code is AuthFailureCode.PII_AUDIT_UNAVAILABLE
    global_pii_query.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_merged_membership_listing_uses_legacy_actor_for_domain_authority(
    merged_membership_compatibility_state,
    pii_read_context,
):
    state = merged_membership_compatibility_state

    [item] = list_users(
        state.tenant_id,
        pii_context=pii_read_context.for_tenant(state.tenant_id),
    )

    actor_pk = state.membership.legacy_actor_user_id
    assert actor_pk == state.source_actor.pk
    assert actor_pk != state.canonical_identity.pk
    assert item.id == actor_pk
    assert item.user_uuid == _pk_to_uuid4(actor_pk)
    assert item.id != state.canonical_identity.pk
    assert not {"pk", "identity_pk", "canonical_user_pk"} & item.model_dump().keys()
    assert item.roles == [state.role.name]


@pytest.mark.django_db(transaction=True)
def test_user_info_does_not_read_identity_pii_when_audit_fails(
    identity_with_two_memberships,
    pii_read_context,
    mocker,
):
    membership = identity_with_two_memberships.membership_a
    global_pii_query = mocker.patch(
        "apps.users.contracts.user_info_impl.load_identity_display_projection",
    )
    mocker.patch(
        "apps.users.contracts.user_info_impl."
        "PiiAccessAuditService.append_batch_and_commit",
        side_effect=RuntimeError("audit unavailable"),
    )

    with pii_read_context.activate_for_tenant(membership.tenant_id):
        with pytest.raises(AuthFlowError) as caught:
            DjangoUserInfoProvider().get_users_display(
                [uuid.UUID(_pk_to_uuid4(membership.legacy_actor_user_id))],
                membership.tenant_id,
            )

    assert caught.value.code is AuthFailureCode.PII_AUDIT_UNAVAILABLE
    global_pii_query.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_tenant_disable_changes_membership_not_global_identity(
    identity_with_two_memberships,
    admin_actor_id,
):
    identity = identity_with_two_memberships.identity
    membership_a = identity_with_two_memberships.membership_a
    disable_user(
        tenant_id=identity_with_two_memberships.tenant_a,
        user_id=membership_a.legacy_actor_user_id,
        actor_id=admin_actor_id,
    )
    identity.refresh_from_db()
    assert identity.security_status == User.SecurityStatus.ACTIVE
    for tenant_id, expected in (
        (identity_with_two_memberships.tenant_a, TenantMembership.Status.DISABLED),
        (identity_with_two_memberships.tenant_b, TenantMembership.Status.ACTIVE),
    ):
        set_tenant_context(tenant_id)
        try:
            membership = TenantMembership.objects.get(
                identity_id=identity.identity_id,
            )
            assert membership.status == expected
        finally:
            clear_tenant_context()


@pytest.mark.django_db(transaction=True)
def test_merged_path_user_id_is_actor_pk_not_canonical_pk(
    merged_membership_compatibility_state,
    admin_actor_id,
):
    state = merged_membership_compatibility_state
    actor_pk = state.membership.legacy_actor_user_id

    disable_user(
        tenant_id=state.tenant_id,
        user_id=actor_pk,
        actor_id=admin_actor_id,
    )

    state.membership.refresh_from_db()
    state.canonical_identity.refresh_from_db()
    assert actor_pk != state.canonical_identity.pk
    assert state.membership.status == TenantMembership.Status.DISABLED
    assert state.canonical_identity.security_status == User.SecurityStatus.ACTIVE
```

- [ ] **Step 2: Implement membership authority while retaining compatibility**

Prepare the initial N+3a compatibility release with legacy columns still
physically present. It moves server-side tenant authority but deliberately
keeps temporary JWT claims and legacy-token validation until the consumer
inventory is signed:

- `list_users()` and `DjangoUserInfoProvider` enter the requested tenant RLS
  context, load memberships by `(tenant_id, is_deleted=False)`, then join to
  global Identity by `identity_id`; they never filter `User.tenant_id`. They
  first enumerate non-PII membership/Identity/actor IDs, durably commit one
  audit per returned subject with the authenticated actor, tenant, fixed reason
  `TENANT_USER_LIST` or `USER_INFO` and request/correlation IDs, then load the
  global PII projection. Audit failure raises typed
  `503 PII_AUDIT_UNAVAILABLE` before that query;
  `list_users()` receives an explicit frozen PII read context from the API.
  The unchanged cross-domain `UserInfoProtocol` signature uses a mandatory
  request/system `PiiReadContext` ContextVar established at the authenticated
  boundary; missing context also fails closed. Background consumers must enter
  an explicit system scope with actor ref, reason, request and correlation IDs;
  there is no anonymous default;
- compatibility `user_uuid`, `RoleBinding` lookup and
  `DjangoUserInfoProvider` input/output IDs use
  `_pk_to_uuid4(membership.legacy_actor_user_id)`, while global Identity
  projection lookup uses `membership.identity_id`; merged actors therefore
  keep historical domain behavior without becoming credential owners;
- frozen v1 `items[].id` is the membership's integer
  `legacy_actor_user_id`. Every list/detail/admin route treats integer path
  `user_id` the same way: it first resolves the tenant-filtered, non-deleted
  membership by `(tenant_id, legacy_actor_user_id=user_id)`, then loads global
  Identity by `membership.identity_id`. It never serializes the canonical
  Identity PK and never resolves a path actor with `User.objects.get(pk=...)`;
- create/disable/enable use that tenant-local actor path; enable first invokes
  the dormant activation guard. Once allowed, they mutate `TenantMembership` and increment
  `membership.authz_version`; disable revokes only grants for that membership;
  `User.is_active`/`security_status` remain global security state;
- role administration continues using the membership's deterministic legacy
  actor UUID, but first proves the canonical Identity has a non-deleted
  membership with that immutable actor in the tenant;
- login/MFA/device code reads Identity, `MfaCredential`, `PreAuthChallenge` and
  `IdentitySession.device_metadata` only;
- new issuance remains `identity-v1` in production. The exact legacy-token
  validator stays temporarily available only for already-issued legacy tokens;
  it is never a fallback after an `identity-v1` validation failure;
- v1 refresh request-body parsing and `IAM_ALLOW_REFRESH_BODY_COMPAT=True` are
  preserved as a deprecated frozen contract; cookie remains preferred and
  body-use telemetry remains token-free;
- `user.*` aliases and temporary access claims `legacy_user_pk`/`username`
  remain during this compatibility deploy. The former claim is still derived
  from `membership.legacy_actor_user_id`; no server authorization path trusts
  it without the membership mapping.

Instrument any temporary adapter access before N+3a with counter-only
`iam_legacy_authority_read_total{authority}` and
`iam_legacy_authority_write_total{authority}` where `authority` is the fixed
enum `tenant|mfa|device|refresh|event_alias|jwt_claim`; labels never contain
identity/tenant/email/token. Keep the instrumented compatibility branches until
the cutoff below; their evidence feeds the pre-cutoff decision.

Add `test_dormant_deferred_membership_cannot_be_enabled_before_finalization`.
It asserts the typed conflict, unchanged membership/canonical versions and no
new grant/session/outbox effect. The paired Task 16 regression finalizes the
same inactive graph and proves normal canonical onboarding may proceed only
after the signed cutoff.

- [ ] **Step 3: Run the compatibility test and contract boundary**

First prove the Task 15 fixtures collect without depending on later cutoff or
migration fixtures, then run the focused compatibility/API suites:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest --fixtures-per-test apps/users/tests/test_membership_backed_user_compatibility.py
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_membership_backed_user_compatibility.py apps/users/tests/test_user_admin_service.py apps/users/tests/test_identity_auth_api.py apps/users/tests/test_deferred_merge_entrypoint_coverage.py apps/common/tests/test_auth_context_boundary.py -v
docker compose -f docker-compose.dev.yml exec backend python manage.py makemigrations --check --dry-run
docker compose -f docker-compose.dev.yml exec -T backend python manage.py export_openapi > /tmp/iam-identity-n3a-compat-openapi.json
python3 infra/scripts/openapi_contract_linter.py --baseline backend/openapi_master.json --candidate /tmp/iam-identity-n3a-compat-openapi.json
git diff --check
```

Expected: PASS; a merged membership returns its actor integer and deterministic
actor UUID, never its canonical Identity integer PK. Migration state remains
`users.0004_identity_contract_constraints` and `makemigrations --check` reports
no Task 15 schema change.

- [ ] **Step 4: Commit, deploy and record the Task 15 rollback boundary**

```bash
git add backend/apps/users/services/auth_service.py backend/apps/users/services/mfa_service.py backend/apps/users/services/user_admin_service.py backend/apps/users/services/deferred_merge_guard.py backend/apps/users/services/pii_access_audit_service.py backend/apps/users/queries/user_queries.py backend/apps/users/contracts/user_info_impl.py backend/apps/users/schemas/auth.py backend/apps/users/api.py backend/apps/users/observability.py backend/apps/users/security/credential_entrypoints.py backend/apps/users/security/credential_entrypoint_manifest.json backend/apps/users/tests/test_membership_backed_user_compatibility.py backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py
git commit -m "feat(iam): move compatibility paths to membership authority"
```

Deploy this commit as its own N+3a compatibility release. Confirm temporary
claim, event-alias and legacy-validator behavior is unchanged and record the
first counter samples before proceeding. Because Task 15 has no DDL or cleanup,
its pre-cutoff rollback is an application rollback: return to the Task 14
artifact while leaving schema `0004` and all Identity/membership data intact.
If any `IdentityMergeOperation` has already reached `APPROVED_DEFERRED`, the
rollback floor is instead the last merge-aware compatibility artifact; never
roll back below the Task 2 canonical-authority guard or remap an immutable
membership actor.

---

### Task 16: Release N+3a Signed Legacy Cutoff, Drain, and Deferred Merge Finalization (No DDL)

**Files:**

- Modify: `backend/apps/users/services/auth_service.py`
- Modify: `backend/apps/users/services/auth_token_codec.py`
- Modify: `backend/apps/users/services/auth_rollout.py`
- Modify: `backend/apps/users/services/recovery_service.py`
- Modify: `backend/apps/users/services/deferred_merge_guard.py`
- Modify: `backend/apps/users/events.py`
- Modify: `backend/apps/users/observability.py`
- Modify: `backend/apps/users/repositories/identity_merge_repository.py`
- Modify: `backend/apps/users/security/identity_merge_locking.py`
- Modify: `backend/apps/users/security/credential_entrypoints.py`
- Modify: `backend/apps/users/security/credential_entrypoint_manifest.json`
- Create: `backend/apps/users/services/identity_merge_finalization_service.py`
- Create: `backend/apps/users/management/commands/finalize_deferred_identity_merges.py`
- Create: `backend/apps/users/management/commands/check_legacy_cutoff_readiness.py`
- Create: `backend/apps/users/management/commands/purge_legacy_refresh_state.py`
- Modify: `backend/apps/common/api/auth.py`
- Modify: `backend/config/settings/base.py`
- Modify: `backend/config/settings/local.py`
- Modify: `backend/config/settings/prod.py`
- Modify: `infra/api_gateway/kong.yml`
- Create: `docs/runbooks/iam-identity-jwt-consumer-inventory.md`
- Modify: `docs/runbooks/iam-identity-release-gates.md`
- Create: `frontend/e2e/specs/auth-legacy-claim-retirement.spec.ts`
- Modify: `frontend/src/shared/api/generated/`
- Test: `backend/apps/users/tests/test_legacy_authority_cutoff.py`
- Test: `backend/apps/users/tests/test_deferred_identity_merge_finalization.py`
- Test: `backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py`
- Modify: `backend/apps/users/tests/conftest.py`
- Modify: `backend/apps/users/tests/iam_factories.py`

**Interfaces:**

- Produces a signed client-consumer inventory and browser acceptance proving
  that removing `username` and `legacy_user_pk` is safe for every client-local
  JWT reader.
- Removes the legacy issuer/validator, temporary access claims and new
  `user.*` aliases in one signed production cutoff. The surviving `user_id`
  remains the deterministic UUID of
  `membership.legacy_actor_user_id`; the frozen v1 refresh request body
  remains accepted.
- Removes the pre-cutoff deferred-source row from every login/recovery
  selector. Raw source `email`/`username` stay in the compatibility actor row
  for history/display only, but no Task 16 query selects that row. Username
  aliases always fail generically. Recovery normalizes the submitted email and
  selects canonical `normalized_email` only: when a historical source spelling
  normalizes to the canonical email, it may create exactly one canonical
  recovery operation; it can never create source authority. A stale
  source-only email returns generic `202` with no token/operation/outbox.
- Produces idempotent `purge_legacy_refresh_state --scan-count 1000` for only
  parsed `auth:refresh:*` keys in Redis DB 4.
- Produces `finalize_deferred_identity_merges --batch-size 500` and
  `check_legacy_cutoff_readiness --window-days 7`. Both are application/data
  operations over the existing `0004` schema and perform no DDL.
- Produces `finalize_deferred_merge(operation_id: UUID) ->
  MergeFinalizationResult`, whose immutable fields are `operation_id`,
  `mode`, `previous_state`, `final_state` and `finalized_at`, plus
  `collect_merge_readiness(operation_ids: Iterable[UUID] | None = None) ->
  MergeReadinessSnapshot` with named integer blocker counts.
- Makes merge readiness status-aware. An active graph requires confirmed
  canonical password and MFA convergence. A currently inactive graph
  (`LOCKED`, `PENDING_CREDENTIALS` or `MFA_SETUP_REQUIRED`) instead requires a
  fully fail-closed/drained graph and never compares, decrypts or requires
  equality of source/canonical password hashes or MFA material, regardless of
  whether its earlier convergence mode was owner-confirmed or dormant.
- Exposes the exact non-PII readiness metric
  `active_deferred_merges_without_confirmed_canonical_mfa` and the exact gate
  count `pending_merge_count`. Task 16 cannot complete until both are zero.
- Replaces the login/recovery/cutoff entry-point manifest paths, reruns static
  coverage and requires a newly signed exact-build attestation before either
  finalization or merge execution is effective on the cutoff artifact.

- [ ] **Step 1: Complete the JWT client-consumer inventory**

Create `docs/runbooks/iam-identity-jwt-consumer-inventory.md` with one row for
the web frontend, generated SDK and every configured external integration.
Each row records owner, token transport, whether it decodes JWT locally,
claims read, migration evidence, acceptance evidence and a signed approval
reference. An empty external list requires an explicit signed
`NO_EXTERNAL_JWT_CONSUMERS` statement; absence of entries is not evidence.

Run and attach the classified output of both source searches to the release
record:

```bash
rg -n --glob '*.{ts,tsx,js,jsx}' 'legacy_user_pk|username|jwtDecode|decodeJwt|claims' frontend/src frontend/e2e
rg -n 'legacy_user_pk|username' frontend/src/shared/api/generated
```

The first search deliberately includes login-form `username` matches; every
match is manually classified as login input, server response field or local
JWT-claim read. The gate requires zero remaining local reads of the two
retired claims. The generated SDK is regenerated after the server contract
changes and the second search is either empty or has every non-claim match
classified.

Before production cutoff, enable the proposed no-temporary-claims issuer in
staging (or an isolated canary that cannot issue production sessions). Add
`auth-legacy-claim-retirement.spec.ts`; through Kong it logs in, completes
MFA/tenant selection, decodes the proposed access token only inside the test,
asserts `username` and `legacy_user_pk` are absent, then proves dashboard
identity display, refresh and A-to-B switching still work from API/store data.
The signed **pre-cutoff** release decision must reference the passing browser
run and every external consumer owner's acceptance; server telemetry cannot
substitute for this inventory because local claim reads are invisible to the
backend. Production keeps the claims until this gate is complete.

- [ ] **Step 2: Write the failing cutoff, event and frozen-v1 tests**

Create `test_legacy_authority_cutoff.py` after extending the Task 16 harness:

```python
import uuid

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from apps.common.models.outbox import OutboxRecord
from apps.users.events import emit_auth_event
from apps.users.models import RecoveryOperation, RecoveryToken
from apps.users.services.auth_token_codec import decode_access_token


@pytest.mark.django_db(transaction=True)
def test_cutoff_access_token_has_only_frozen_actor_claim(
    candidate_cutoff_identity_tokens,
):
    claims = decode_access_token(candidate_cutoff_identity_tokens.access)
    membership = candidate_cutoff_identity_tokens.membership
    assert "legacy_user_pk" not in claims
    assert "username" not in claims
    assert claims["user_id"] == str(
        uuid.UUID(
            int=0x00000000000040008000000000000000
            | (membership.legacy_actor_user_id & 0xFFFFFFFFFFFF)
        )
    )


@pytest.mark.django_db(transaction=True)
def test_cutoff_never_falls_back_to_legacy_validator(
    protected_backend_client,
    candidate_token_with_invalid_identity_v1_signature,
):
    response = protected_backend_client.get(
        "/api/v1/auth/sessions",
        headers={"Authorization": f"Bearer {candidate_token_with_invalid_identity_v1_signature}"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "TOKEN_SIGNATURE_INVALID"


@pytest.mark.django_db(transaction=True)
def test_identity_event_no_longer_emits_user_alias(identity_created_event):
    emit_auth_event(identity_created_event)
    assert OutboxRecord.objects.filter(event_type="identity.created").exists()
    assert not OutboxRecord.objects.filter(event_type="user.created").exists()


@pytest.mark.django_db(transaction=True)
def test_purge_leaves_ambiguous_refresh_key_and_reports_failure(redis):
    redis.setex("auth:refresh:not-an-int:not-a-uuid", 3600, "opaque")
    with pytest.raises(CommandError, match="ambiguous_legacy_refresh_keys"):
        call_command("purge_legacy_refresh_state", "--scan-count", "2")
    assert redis.exists("auth:refresh:not-an-int:not-a-uuid")


@override_settings(IAM_ALLOW_REFRESH_BODY_COMPAT=True)
def test_cutoff_v1_refresh_still_accepts_frozen_body_contract(
    client,
    valid_refresh_token,
):
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": valid_refresh_token},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.json()["token_type"] == "bearer"


@pytest.mark.django_db(transaction=True)
def test_cutoff_source_alias_no_longer_authenticates(
    deferred_merge_with_raw_source_alias_at_cutoff,
    client,
):
    state = deferred_merge_with_raw_source_alias_at_cutoff
    assert state.operation.state == "approved_deferred"
    denied = client.post(
        "/api/v1/auth/login",
        json={
            "username": state.source.username,
            "password": state.canonical_password,
            "device_fingerprint": "cutoff-source-alias",
        },
    )
    assert denied.status_code == 401
    assert denied.json()["code"] == "INVALID_CREDENTIALS"
    assert state.login_with_canonical_identifier().status_code == 200


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/recovery/password/start",
        "/api/v1/auth/recovery/mfa/start",
    ],
)
def test_cutoff_shared_email_spelling_creates_only_canonical_recovery(
    deferred_merge_with_shared_normalized_source_email_at_cutoff,
    client,
    path,
):
    state = deferred_merge_with_shared_normalized_source_email_at_cutoff

    accepted = client.post(
        path,
        json={"email": state.source.email},
    )

    assert accepted.status_code == 202
    token = RecoveryToken.objects.get(identity_id=state.canonical.identity_id)
    assert token.operation.identity_id == state.canonical.identity_id
    assert not RecoveryToken.objects.filter(
        identity_id=state.source_identity_id,
    ).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/recovery/password/start",
        "/api/v1/auth/recovery/mfa/start",
    ],
)
def test_cutoff_stale_source_only_email_is_generic_without_artifact(
    deferred_merge_with_stale_source_email_at_cutoff,
    client,
    path,
):
    state = deferred_merge_with_stale_source_email_at_cutoff
    token_count = RecoveryToken.objects.count()
    operation_count = RecoveryOperation.objects.count()
    outbox_count = OutboxRecord.objects.count()

    accepted = client.post(
        path,
        json={"email": state.source.email},
    )

    assert accepted.status_code == 202
    assert RecoveryToken.objects.count() == token_count
    assert RecoveryOperation.objects.count() == operation_count
    assert OutboxRecord.objects.count() == outbox_count
```

First run `pytest --fixtures-per-test` for both Task 16 modules; fixture
collection must succeed before the red run.

- [ ] **Step 3: Implement the signed JWT cutoff and exact drain procedure**

Only after Step 1's signed pre-cutoff gate, prepare one production cutoff that:

- removes `legacy_user_pk` and `username` from newly issued access JWTs;
- derives the surviving `user_id` from
  `membership.legacy_actor_user_id` and validates it through that mapping;
- stops the legacy issuer and validator and removes
  `LEGACY_JWT_SIGNING_KEY`; an invalid identity-v1 token never falls back;
- removes deferred source rows from all login/recovery selector queries while
  leaving raw compatibility columns unchanged. Source username returns generic
  invalid credentials. Recovery performs canonical normalized-email lookup
  only: an input equal after v1 normalization may start canonical recovery,
  while a stale source-only address returns the same generic `202` as unknown
  input with no artifact. Neither path ever selects source Identity authority;
- stops new `user.*` compatibility aliases while preserving historical
  outbox/audit records.

Then, in this exact order:

1. revoke pre-cutoff/legacy session families with
   `revoke_identity_v1_sessions --reason legacy-authority-retirement`;
2. run `purge_legacy_refresh_state --scan-count 1000`; it uses Redis `SCAN`
   only in DB 4, deletes only keys matching parsed
   `auth:refresh:<integer-pk>:<uuid-jti>`, reports counts only and is
   idempotent;
3. wait at least 15 minutes (the maximum old access JWT lifetime) after gateway
   stops accepting the legacy issuer and after session revocation;
4. prove no configured consumer subscribes to `user.*` compatibility aliases.

Do not use Redis `KEYS`, flush a DB or log key suffixes. Any ambiguous key is
left untouched and makes the retirement gate fail.

- [ ] **Step 4: Write status-aware deferred-merge readiness tests**

Create `test_deferred_identity_merge_finalization.py` with active, dormant,
mixed-state, hold and pending-operation fixtures:

```python
import pytest
from django.core.management import CommandError, call_command

from apps.users.models import IdentityMergeOperation, User
from apps.users.services.auth_errors import AuthFlowError
from apps.users.services.identity_merge_finalization_service import (
    MergeFinalizationBlocked,
    collect_merge_readiness,
    finalize_deferred_merge,
)


@pytest.mark.django_db(transaction=True)
def test_active_deferred_merge_requires_confirmed_canonical_mfa(
    active_deferred_merge_without_canonical_mfa,
):
    operation = active_deferred_merge_without_canonical_mfa.operation

    readiness = collect_merge_readiness([operation.id])

    assert readiness.active_deferred_merges_without_confirmed_canonical_mfa == 1
    assert readiness.pending_merge_count == 1
    with pytest.raises(MergeFinalizationBlocked, match="confirmed canonical MFA"):
        finalize_deferred_merge(operation.id)
    operation.refresh_from_db()
    assert operation.state == IdentityMergeOperation.State.APPROVED_DEFERRED


@pytest.mark.django_db(transaction=True)
def test_active_deferred_merge_requires_canonical_password_convergence(
    active_deferred_merge_without_canonical_password_proof,
):
    operation = active_deferred_merge_without_canonical_password_proof.operation
    assert operation.convergence_mode == "owner_confirmed"
    assert operation.credentials_converged_at is None

    with pytest.raises(
        MergeFinalizationBlocked,
        match="canonical password convergence",
    ):
        finalize_deferred_merge(operation.id)
    operation.refresh_from_db()
    assert operation.state == IdentityMergeOperation.State.APPROVED_DEFERRED


@pytest.mark.django_db(transaction=True)
def test_ready_active_finalizer_only_marks_operation_finalized(
    ready_active_deferred_merge,
):
    state = ready_active_deferred_merge
    before = (
        state.source.email,
        state.source.username,
        state.membership.identity_id,
        state.membership.legacy_actor_user_id,
    )

    finalize_deferred_merge(state.operation.id)

    state.refresh()
    assert state.operation.state == IdentityMergeOperation.State.FINALIZED
    assert (
        state.source.email,
        state.source.username,
        state.membership.identity_id,
        state.membership.legacy_actor_user_id,
    ) == before


@pytest.mark.django_db(transaction=True)
def test_dormant_inactive_merge_finalizes_without_hash_or_mfa_equality(
    drained_dormant_deferred_merge_with_distinct_shadows,
    mocker,
):
    state = drained_dormant_deferred_merge_with_distinct_shadows
    password_compare = mocker.patch.object(User, "check_password")
    mfa_decrypt = mocker.patch(
        "apps.users.services.auth_keyring.AuthKeyring.decrypt",
    )

    result = finalize_deferred_merge(state.operation.id)

    state.operation.refresh_from_db()
    assert result.mode == "dormant_fail_closed"
    assert state.operation.state == IdentityMergeOperation.State.FINALIZED
    password_compare.assert_not_called()
    mfa_decrypt.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_owner_converged_identity_locked_later_uses_inactive_readiness(
    drained_owner_converged_merge_locked_afterwards,
    mocker,
):
    state = drained_owner_converged_merge_locked_afterwards
    assert state.operation.convergence_mode == "owner_confirmed"
    assert state.canonical.security_status == User.SecurityStatus.LOCKED
    password_compare = mocker.patch.object(User, "check_password")
    mfa_decrypt = mocker.patch(
        "apps.users.services.auth_keyring.AuthKeyring.decrypt",
    )

    result = finalize_deferred_merge(state.operation.id)

    assert result.mode == "inactive_fail_closed"
    password_compare.assert_not_called()
    mfa_decrypt.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "blocker",
    ["open_session", "open_recovery", "unresolved_reconciliation", "legal_hold"],
)
def test_dormant_merge_requires_complete_fail_closed_drain(
    dormant_deferred_merge_with_blocker,
    blocker,
):
    operation = dormant_deferred_merge_with_blocker(blocker).operation

    with pytest.raises(MergeFinalizationBlocked, match=blocker):
        finalize_deferred_merge(operation.id)
    operation.refresh_from_db()
    assert operation.state == IdentityMergeOperation.State.APPROVED_DEFERRED


@pytest.mark.django_db(transaction=True)
def test_dormant_graph_can_activate_only_after_cutoff_finalization(
    drained_dormant_deferred_merge_at_cutoff,
):
    state = drained_dormant_deferred_merge_at_cutoff

    with pytest.raises(
        AuthFlowError,
        match="MERGE_DORMANT_FINALIZATION_REQUIRED",
    ):
        state.complete_canonical_onboarding()

    finalize_deferred_merge(state.operation.id)
    state.refresh()
    assert state.operation.state == IdentityMergeOperation.State.FINALIZED
    activated = state.complete_canonical_onboarding()
    assert activated.canonical_status == User.SecurityStatus.ACTIVE
    assert activated.membership_status == "active"


@pytest.mark.django_db(transaction=True)
def test_cutoff_gate_requires_zero_pending_merges(pending_identity_merge):
    readiness = collect_merge_readiness([pending_identity_merge.id])
    assert readiness.pending_merge_count == 1
    with pytest.raises(CommandError, match="pending_merge_count"):
        call_command("check_legacy_cutoff_readiness", "--window-days", "7")
```

The metrics test uses an isolated registry and asserts the gauge named exactly
`active_deferred_merges_without_confirmed_canonical_mfa` has value `1` for the
first fixture and `0` after a ready active graph finalizes. It has no identity,
tenant, email, operation or token labels.

- [ ] **Step 5: Implement locked, status-aware deferred finalization**

`finalize_deferred_merge(operation_id: UUID) -> MergeFinalizationResult` uses
the shared merge-graph lock coordinator and first revalidates Task 2's signed,
unexpired runtime entry-point coverage attestation for the deployed build. It
takes the sorted advisory/row locks,
re-reads the complete graph and all holds, and permits only
`APPROVED_DEFERRED -> READY_TO_FINALIZE -> FINALIZED`. The two transitions and
their append-only audit evidence commit atomically. Finalization changes only
the operation state/finalized timestamp and writes evidence: it does not
transform raw source `email`/`username`, change source/canonical password or
MFA data, move membership ownership again, change
`TenantMembership.legacy_actor_user_id`, rewrite a domain FK/RoleBinding or
drop a column. Concurrent graph expansion returns typed retry. A hold appends
`IdentityMergeHoldBlock` without mutating the held aggregate; only a signed
requeue can cause a later attempt.

The service raises typed `MergeFinalizationBlocked(code, blocker_counts)` with
a closed code for canonical password convergence, canonical MFA, dormant
artifact drain, unresolved reconciliation, legal hold, inconsistent graph or
concurrent retry. It contains IDs neither in its message nor metric labels.
Management commands map that typed result to `CommandError`; repository or
crypto exception strings are never forwarded.

Readiness has exactly two closed branches:

- **Active owner-confirmed:** the canonical Identity is `ACTIVE`; every active
  membership resolves to it; `operation.convergence_mode` is
  `owner_confirmed`; `operation.credential_epoch == 1` and
  `operation.credentials_converged_at` is non-null evidence that the
  owner-confirmed password ceremony wrote the canonical hash; the canonical
  row has a usable password; and a confirmed, non-revoked, encrypted
  `MfaCredential` selected by `identity_id=canonical_identity_id` exists. No
  new merge-to-MFA foreign key is introduced. Source compatibility passwords,
  legacy MFA flags/secrets and source MFA rows are not accepted as proof. If
  canonical MFA proof is missing, increment
  `active_deferred_merges_without_confirmed_canonical_mfa` and leave the
  operation deferred. A missing password proof is a separate named blocker.
- **Inactive fail-closed:** canonical status is `LOCKED`,
  `PENDING_CREDENTIALS` or `MFA_SETUP_REQUIRED`, canonical and every source
  are non-authenticating, every membership is `DISABLED` or `DRAFT`, every
  session/grant and
  Redis refresh family is revoked/expired/absent, every pre-auth challenge is
  expired/consumed, every recovery token/operation is terminal, every session
  reconciliation row is resolved, and no operation/Identity/source/security/
  tenant row is held. This branch requires no password-hash or MFA-secret
  equality and does not call password comparison or MFA decryption. It applies
  whether the original convergence mode was `dormant_fail_closed` or an
  `owner_confirmed` Identity was subsequently locked/deactivated; finalization
  readiness follows current security status, not historical ceremony name.
  Different residual shadow values cannot promote the graph to active
  authority.

For `convergence_mode=dormant_fail_closed`, the Task 2 activation guard makes
the inactive branch the only reachable pre-finalization branch. Every MFA
confirmation, global unlock and membership enable remains blocked while the
operation is deferred/ready. Once the signed cutoff is active and this service
finalizes the fully drained graph, the guard permits ordinary canonical
onboarding; finalization never itself activates a user or membership.

A canonical `ACTIVE` Identity or any active membership classifies the graph as
active even though source compatibility rows are non-authenticating shadows.
An inconsistent graph with an inactive canonical Identity but an active source
or membership matches neither branch and fails closed for an explicit
owner-confirmed remediation ceremony.
`collect_merge_readiness()` counts as pending every operation in
`APPROVED_PENDING_CONVERGENCE`, `APPROVED_DEFERRED` or `READY_TO_FINALIZE`;
`FINALIZED`, `CANCELLED` and `EXPIRED` are terminal. The command reports the
exact non-secret `pending_merge_count` and exits non-zero until it is zero.
Tenant-scoped membership/grant blockers are enumerated only through the Task 4
`DurableIamTenantInventoryProvider`, one entered RLS tenant at a time; neither
the service nor either command uses an unscoped tenant manager.

- [ ] **Step 6: Run cutoff and finalization verification**

Before committing the release artifact, run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest --fixtures-per-test apps/users/tests/test_legacy_authority_cutoff.py apps/users/tests/test_deferred_identity_merge_finalization.py
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_legacy_authority_cutoff.py apps/users/tests/test_deferred_identity_merge_finalization.py apps/users/tests/test_deferred_merge_entrypoint_coverage.py apps/users/tests/test_auth_rollout.py apps/users/tests/test_identity_auth_api.py -v
docker compose -f docker-compose.dev.yml exec backend python manage.py makemigrations --check --dry-run
docker compose -f docker-compose.dev.yml exec -T backend python manage.py export_openapi > /tmp/iam-identity-n3a-cutoff-openapi.json
python3 infra/scripts/openapi_contract_linter.py --baseline backend/openapi_master.json --candidate /tmp/iam-identity-n3a-cutoff-openapi.json
make test-e2e
make generate-sdk
make tsc
git diff --check
```

- [ ] **Step 7: Commit, deploy and record the Task 16 rollback boundary**

```bash
git add backend/apps/users/services/auth_service.py backend/apps/users/services/auth_token_codec.py backend/apps/users/services/auth_rollout.py backend/apps/users/services/recovery_service.py backend/apps/users/services/deferred_merge_guard.py backend/apps/users/events.py backend/apps/users/observability.py backend/apps/users/repositories/identity_merge_repository.py backend/apps/users/security/identity_merge_locking.py backend/apps/users/security/credential_entrypoints.py backend/apps/users/security/credential_entrypoint_manifest.json backend/apps/users/services/identity_merge_finalization_service.py backend/apps/users/management/commands/finalize_deferred_identity_merges.py backend/apps/users/management/commands/check_legacy_cutoff_readiness.py backend/apps/users/management/commands/purge_legacy_refresh_state.py backend/apps/common/api/auth.py backend/config/settings/base.py backend/config/settings/local.py backend/config/settings/prod.py infra/api_gateway/kong.yml backend/apps/users/tests/test_legacy_authority_cutoff.py backend/apps/users/tests/test_deferred_identity_merge_finalization.py backend/apps/users/tests/test_deferred_merge_entrypoint_coverage.py backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py frontend/e2e/specs/auth-legacy-claim-retirement.spec.ts frontend/src/shared/api/generated docs/runbooks/iam-identity-jwt-consumer-inventory.md docs/runbooks/iam-identity-release-gates.md
git commit -m "feat(iam): cut off legacy auth and finalize deferred merges"
```

Deploy only the signed artifact and execute Step 3's drain exactly once per
recorded release attempt; all commands are idempotent on retry. After the
drain, finalize ready operations, then collect seven full consecutive days of
cutoff evidence and run:

```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py finalize_deferred_identity_merges --batch-size 500
docker compose -f docker-compose.dev.yml exec backend python manage.py check_legacy_cutoff_readiness --window-days 7
```

The readiness command succeeds only when Task 14 stabilization still passes,
legacy authority read/write and legacy-validator counters are zero for seven
full consecutive days, parsed legacy refresh keys and ambiguous matches are
zero, the gateway accepts only the current issuer, the event inventory has no
`user.*` subscriber, all signed JWT consumer acceptance rows are closed,
`active_deferred_merges_without_confirmed_canonical_mfa == 0`, every other
active/dormant readiness blocker is zero and `pending_merge_count == 0`.

Task 16 has no DDL and leaves schema `0004`, but session revocation, removal of
the trusted legacy validator and merge finalization are an operational one-way
boundary.
Rollback may use only an `ENFORCE`, identity-only, merge-aware artifact that
does not re-enable the legacy issuer/validator, recreate claims/aliases,
resurrect refresh state or reverse a finalized membership graph. If the cutoff
artifact fails, keep legacy tokens rejected and roll forward/fail closed.

---

### Task 17: Release N+3b Fresh Retirement Gates and Irreversible Legacy DDL

**Files:**

- Modify: `backend/apps/users/models.py`
- Create: `backend/apps/users/migrations/0005_retire_legacy_user_authority.py`
- Create: `backend/apps/users/management/commands/check_legacy_authority_retirement.py`
- Modify: `backend/apps/users/infrastructure/iam_tenant_inventory.py`
- Modify: `backend/apps/users/security/iam_retirement_barrier.py`
- Modify: `docs/runbooks/iam-identity-release-gates.md`
- Test: `backend/apps/users/tests/test_legacy_authority_retirement.py`
- Test: `backend/apps/users/tests/test_identity_retirement_migration.py`
- Modify: `backend/apps/users/tests/conftest.py`
- Modify: `backend/apps/users/tests/iam_factories.py`

**Interfaces:**

- Produces `check_legacy_authority_retirement --window-days 7
  --cutoff-evidence /protected/path/evidence.json --expected-build-commit
  <sha>` as a fresh hard gate immediately before destructive schema removal;
  a successful Task 16 result is input evidence, never a reusable waiver.
- Reuses the exact Task 4 `DurableIamTenantInventoryProvider` for final per-tenant RLS
  checks and rechecks `pending_merge_count == 0`, status-aware merge readiness,
  all legal holds, cutoff/drain state, backup/restore/retention evidence and
  the signed consumer inventories against the currently deployed commit.
- Removes only inactive legacy tenant/MFA/device columns and indexes. It keeps
  `users.User.password`, integer PK, stable `identity_id`, global security
  state and the actor rows referenced by
  `TenantMembership.legacy_actor_user_id`.
- Makes `0005` explicitly irreversible: its backwards path raises Django
  `IrreversibleError`. Its first SQL takes the exclusive
  `IAM_RETIREMENT_BARRIER`, then its in-migration forward preflight rechecks
  every global and per-registry-tenant IAM hold in the same transaction before
  DDL. The fresh command still covers signed cutoff/backup evidence as an
  external mandatory release-orchestrator gate.

- [ ] **Step 1: Write failing fresh-gate and contracted-schema tests**

```python
import pytest
from django.core.management import CommandError, call_command
from django.db import connection

from apps.users.models import IdentityMergeOperation, User


def retirement_args(signed_cutoff_evidence) -> list[str]:
    return [
        "--window-days",
        "7",
        "--cutoff-evidence",
        str(signed_cutoff_evidence.path),
        "--expected-build-commit",
        signed_cutoff_evidence.build_commit,
    ]


def test_runtime_user_model_has_no_legacy_authority_fields():
    names = {field.name for field in User._meta.fields}
    assert "tenant_id" not in names
    assert "mfa_enabled" not in names
    assert "mfa_secret" not in names
    assert "device_fingerprint" not in names
    assert {"id", "password", "username", "email", "identity_id"} <= names


@pytest.mark.django_db
def test_contract_migration_removed_legacy_columns_and_indexes():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'users_users'
              AND column_name = ANY(%s)
            """,
            [["tenant_id", "mfa_enabled", "mfa_secret", "device_fingerprint"]],
        )
        assert cursor.fetchall() == []
        cursor.execute(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE tablename = 'users_users'
              AND indexname = ANY(%s)
            """,
            [["idx_users_tenant", "idx_users_tenant_username"]],
        )
        assert cursor.fetchall() == []


@pytest.mark.django_db(transaction=True)
def test_retirement_gate_rejects_remaining_legacy_refresh_keys(
    redis,
    signed_cutoff_evidence,
):
    redis.setex("auth:refresh:1:00000000-0000-4000-8000-000000000001", 3600, "1")
    with pytest.raises(CommandError, match="legacy_refresh_keys"):
        call_command(
            "check_legacy_authority_retirement",
            *retirement_args(signed_cutoff_evidence),
        )


@pytest.mark.django_db(transaction=True)
def test_retirement_gate_rejects_any_held_identity(
    held_legacy_identity,
    signed_cutoff_evidence,
):
    with pytest.raises(CommandError, match="held_identity_rows"):
        call_command(
            "check_legacy_authority_retirement",
            *retirement_args(signed_cutoff_evidence),
        )


@pytest.mark.django_db(transaction=True)
def test_retirement_gate_requires_zero_pending_merges(
    pending_identity_merge,
    signed_cutoff_evidence,
):
    assert pending_identity_merge.state in {
        IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE,
        IdentityMergeOperation.State.APPROVED_DEFERRED,
        IdentityMergeOperation.State.READY_TO_FINALIZE,
    }
    with pytest.raises(CommandError, match="pending_merge_count"):
        call_command(
            "check_legacy_authority_retirement",
            *retirement_args(signed_cutoff_evidence),
        )


@pytest.mark.django_db(transaction=True)
def test_retirement_gate_rejects_stale_or_wrong_build_evidence(
    stale_signed_cutoff_evidence,
):
    with pytest.raises(CommandError, match="cutoff_evidence_stale_or_mismatched"):
        call_command(
            "check_legacy_authority_retirement",
            *retirement_args(stale_signed_cutoff_evidence),
        )
```

Add a registry-completeness fixture with tenant C present only in
`IamTenantRegistryEntry`: its held `DRAFT` membership has no legacy
`User.tenant_id`, RoleBinding or switch-operation reference. Both the runtime
retirement gate and the migration's historical preflight must enter tenant C
and reject retirement.

Add two PostgreSQL-connection race tests using real commits, bounded
`lock_timeout` and no mocked lock layer. The first lets a membership/session/
grant hold transaction acquire the shared barrier before `0005`; migration
must wait and then abort before any DDL with every legacy column/value intact.
The second lets `0005` acquire the exclusive barrier first; the hold latch
must remain blocked until migration commits. There is no allowed execution in
which a committed pre-existing hold coexists with successful destructive DDL.
The tests assert the exact frozen advisory key in the Task 1 runtime helper,
every hold trigger and migration `0005` are identical.

- [ ] **Step 2: Implement and run the fresh retirement gate**

`check_legacy_authority_retirement --window-days 7 --cutoff-evidence PATH
--expected-build-commit SHA` exits non-zero unless:

- Task 14 stabilization still passes and no reconciliation row is unresolved;
- Task 16's status-aware query reports every active finalized graph has a
  usable canonical password, non-null `credentials_converged_at` and confirmed
  canonical encrypted MFA factor; every currently inactive finalized graph
  remains fully fail-closed/drained without requiring password-hash or MFA
  equality, regardless of its historical convergence mode;
- `active_deferred_merges_without_confirmed_canonical_mfa == 0`, every other
  active/dormant merge blocker is zero and `pending_merge_count == 0` for all
  `APPROVED_PENDING_CONVERGENCE`, `APPROVED_DEFERRED` and
  `READY_TO_FINALIZE` operations;
- `held_identity_rows == 0` and every named merge-operation, source,
  membership, session, grant, recovery and reconciliation hold count is zero;
  a legal hold cannot be waived by this command or by a release approver, and
  Plan 1 has no archive substitute;
- membership-backed list/admin/user-info contract tests pass for multi-tenant
  Identity;
- legacy read/write and legacy-validator counters are zero for seven full days;
- no parsed or ambiguous `auth:refresh:*` key remains;
- gateway rejects legacy/missing issuer tokens and accepts the current token
  without `legacy_user_pk`;
- event-consumer inventory has no `user.*` subscriber and no new alias was
  emitted during the window;
- the final Task 4 `DurableIamTenantInventoryProvider` pass enters every inventoried
  tenant and the Task 14 membership/grant/policy invariants remain zero;
- the signed JWT consumer inventory lists frontend, generated SDK and all
  external integrations, has no unresolved owner, and records passing browser
  acceptance without `username` or `legacy_user_pk`;
- a source scan finds no runtime reference to `User.tenant_id`,
  `mfa_enabled`, `mfa_secret`, `device_fingerprint` or
  `LEGACY_JWT_SIGNING_KEY` outside old
  migrations and historical docs/tests explicitly marked pre-retirement;
- the release record contains signed cutoff approval plus verified backup,
  restore-test and retention evidence for the irreversible migration;
- the Ed25519-signed cutoff evidence was issued after this final preflight
  began, is at most 30 minutes old, is unexpired and binds the deployed build
  commit, target `users.0005_retire_legacy_user_authority`, JWT/event inventory
  digests, Task 16 readiness digest, gateway snapshot and backup/restore/
  retention references. A prior successful Task 16 command, screenshot or
  release ticket is not fresh evidence.

The command verifies external evidence and then performs all DB/Redis/RLS
queries again on primary. Its success record contains the evidence digest,
deployed commit, target migration, start/completion timestamps and named
non-secret zero counts. It never embeds email, token, Redis key suffix or MFA
material. If 30 minutes elapse, the deployment changes, or any gate-relevant
state changes before migration start, discard the result and run a new signed
preflight. This result is mandatory release evidence, but it is not the
concurrency authority for a later legal-hold latch; only the database barrier
inside `0005` closes that race.

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_legacy_authority_retirement.py -k 'retirement_gate' -v
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_membership_backed_user_compatibility.py apps/users/tests/test_identity_stabilization_gate.py -v
docker compose -f docker-compose.dev.yml exec backend python manage.py purge_legacy_refresh_state --scan-count 1000
docker compose -f docker-compose.dev.yml exec backend python manage.py check_legacy_authority_retirement --window-days 7 --cutoff-evidence "$IAM_CUTOFF_EVIDENCE_PATH" --expected-build-commit "$IAM_DEPLOYED_COMMIT"
docker compose -f docker-compose.dev.yml exec -T backend python manage.py export_openapi > /tmp/iam-identity-n3-openapi.json
python3 infra/scripts/openapi_contract_linter.py --baseline backend/openapi_master.json --candidate /tmp/iam-identity-n3-openapi.json
make test-e2e
```

- [ ] **Step 3: Implement the inactive-column contract migration**

Only after Task 16 has completed and Task 17's fresh signed gate succeeds,
create N+3b migration
`0005_retire_legacy_user_authority.py` removing:

- `User.tenant_id` and indexes `idx_users_tenant`,
  `idx_users_tenant_username`;
- `User.mfa_enabled` and plaintext `User.mfa_secret`;
- global `User.device_fingerprint`.

The migration does not remove `password`, integer PK, `identity_id`, global
Identity status/version, raw compatibility `email`/`username`, any actor
`User` row or Django compatibility fields. It never changes the immutable
`TenantMembership.legacy_actor_user` FK. Run the migration linter against the
exact file. Because these columns are proven inactive by a
separate code release and soak, any linter rule that still classifies the
`RemoveField` operations as active requires a narrowly scoped ADR-0024 release
approval for this migration; never suppress `RemoveField` globally or hide an
unrelated migration failure.

The migration is atomic and its first SQL statement is exactly the
transaction-scoped exclusive advisory acquisition for the frozen
`IAM_RETIREMENT_BARRIER_KEY = 0x49414D5F52455449`. Migration code embeds this
literal and uses only the historical `apps_registry`,
`schema_editor.connection` and its `using` alias; it imports no runtime model,
inventory provider or barrier helper. Every IAM hold-latch trigger takes the
matching transaction-scoped shared lock before inserting a held row or
changing `FALSE/NULL -> TRUE`.

While retaining the exclusive barrier, the forward preflight locks the frozen
catalog of global and tenant IAM hold-bearing tables in stable table-name
order with `LOCK TABLE ... IN SHARE ROW EXCLUSIVE MODE`. It checks
`users_users.identity_legal_hold` and every global `is_legal_hold` column
first. It then reads
`users_iam_tenant_registry.tenant_id` in UUID order and, for each registry
entry, sets the parameterized local RLS tenant context, checks every frozen
tenant IAM hold-bearing table (including membership, authorization state,
grant and existing RoleBinding where it has a hold latch), and restores the
context before continuing. Registry absence or catalog/schema drift raises a
stable error; neither is treated as an empty tenant. All counts must be zero
before the first index or column drop.

The shared/exclusive ordering gives exactly two legal outcomes. A hold that
acquires the shared barrier first commits before migration can continue and is
seen by the in-transaction preflight, so `0005` aborts at schema `0004`. If
`0005` acquires the exclusive barrier first, a new hold latch waits until the
destructive transaction commits. The external command gate and this
in-migration preflight are independent defenses; the former supplies fresh
signed release evidence, while the latter is the database concurrency
authority. Hold release remains a separately authorized legal action; adding
a legal archive is outside Plan 1 and requires its own ADR.

Implement the destructive operations as an explicitly irreversible migration:
the backwards callable raises `django.db.migrations.exceptions.IrreversibleError`
and `SeparateDatabaseAndState` must not pretend the removed data can be
recreated. Put a forward no-op/reverse-raise sentinel **after** all forward
operations so Django encounters it first when reversing; no reverse state or
DDL runs before the exception. The forward legal-hold preflight
remains the first operation. In `test_identity_retirement_migration.py`, migrate an isolated
PostgreSQL state to `0004`, populate all legacy fields on a held Identity, and
assert applying `0005` fails before DDL and preserves both columns and values.
After clearing the hold in a separate fixture, assert forward succeeds; then
assert an attempted `0005 -> 0004`
reverse raises `IrreversibleError`. Restore the suite's leaf migrations in
`finally`.

- [ ] **Step 4: Run isolated migration, contract and full IAM verification**

```bash
docker compose -f docker-compose.dev.yml run --rm --no-deps -v "$PWD:/workspace:ro" -w /workspace --entrypoint python backend infra/scripts/migration_linter.py
docker compose -f docker-compose.dev.yml exec backend python manage.py migrate --noinput
docker compose -f docker-compose.dev.yml exec backend pytest apps/users/tests/test_identity_retirement_migration.py apps/users/tests -v
docker compose -f docker-compose.dev.yml exec -T backend python manage.py export_openapi > /tmp/iam-identity-n3-openapi.json
python3 infra/scripts/openapi_contract_linter.py --baseline backend/openapi_master.json --candidate /tmp/iam-identity-n3-openapi.json
docker compose -f docker-compose.dev.yml exec backend python manage.py makemigrations --check --dry-run
make generate-sdk
make tsc
git diff --check
```

- [ ] **Step 5: Commit, deploy and record the irreversible rollback boundary**

`0005` is the explicit, narrow ADR-0024 exception to ADR-0002's ordinary
reversibility requirement. It is authorized only after the signed cutoff and
retirement gates, zero held global and tenant IAM rows, verified
backup/restore and retention evidence; the migration itself enforces the hold
gate under the exclusive retirement barrier and Django enforces the
`IrreversibleError` reverse path. Record the signed N+3a/N+3b evidence, event
consumer inventory, JWT client-consumer inventory and owner approvals, metric
windows, `pending_merge_count=0`, gateway drain time and migration-lint
disposition in `docs/runbooks/iam-identity-release-gates.md`.

```bash
git add backend/apps/users/models.py backend/apps/users/migrations/0005_retire_legacy_user_authority.py backend/apps/users/management/commands/check_legacy_authority_retirement.py backend/apps/users/infrastructure/iam_tenant_inventory.py backend/apps/users/security/iam_retirement_barrier.py backend/apps/users/tests/test_legacy_authority_retirement.py backend/apps/users/tests/test_identity_retirement_migration.py backend/apps/users/tests/conftest.py backend/apps/users/tests/iam_factories.py docs/runbooks/iam-identity-release-gates.md
git commit -m "feat(iam): contract retired legacy authority schema"
```

Deploy this commit as a separate N+3b artifact that understands both pre-DDL
`0004` and post-DDL `0005`. Immediately before production migration, create
new signed evidence and rerun Step 2 against that exact deployed commit; then
run `migrate --noinput` within the 30-minute evidence window. A gate failure
leaves production at `0004` and the Task 16 identity-only application remains
safe; correct the evidence/state and issue a new signed attempt.

After `0005` begins successfully there is no ordinary schema rollback. Never
fake-apply `0004`, recreate empty columns, re-enable a legacy validator or
reverse finalized merges. Application rollback is limited to a known-good
identity-only artifact that explicitly supports schema `0005` and keeps
version validation fail-closed. A verified backup restore is a separately
authorized disaster-recovery event for the whole consistent system, not the
Task 17 rollback procedure.

**Release N+3 completion gate:** Plan 1 is complete only after N+3b has soaked
successfully in production. IAM-EMPLOYEES must not begin before this gate.

---

## Final Acceptance

- Expand migration is nullable/non-unique; final `UNIQUE`/`NOT NULL` constraints exist only in the separately released contract migration.
- Duplicate normalized emails and invalid candidates are reported separately
  without aborting the scan; signed approval creates only
  `APPROVED_PENDING_CONVERGENCE` and changes no login, email, membership or
  credential authority. A pending operation remains unresolved and blocks
  contract migration.
- The only merge point is owner-confirmed or fully dormant fail-closed
  convergence. It moves canonical membership ownership, changes only source
  `normalized_email` to the reserved synthetic namespace, preserves the raw
  compatibility actor row and immutable tenant-local `legacy_actor_user` plus
  its `RoleBinding`/domain authority, and establishes canonical-only password,
  MFA, status, recovery, device, session and token authority. Overlapping
  tenant footprints are rejected rather than guessed.
- After convergence, old source/canonical credentials and every prior access,
  refresh and MFA-partial artifact fail. Each later security mutation follows
  the closed effect matrix; source credential fields are non-authoritative
  shadows. Task 16 removes source rows from login/recovery selection without
  rewriting historical database values: usernames fail, recovery targets a
  canonical normalized email only, and stale source-only email returns generic
  `202` without an artifact.
- A dormant deferred/ready graph cannot become active and therefore cannot get
  stuck in the active finalization branch. It finalizes while fully drained at
  Task 16; only afterward may canonical onboarding or membership enablement
  proceed.
- Email normalization is trim → Unicode NFKC → casefold, validates syntax and
  the 254-character limit, and does not apply provider aliases.
- Per-row UUID backfill is idempotent, does not duplicate Identity email into
  membership, and creates exactly one `TenantAuthorizationState` per
  membership tenant; every contracted membership has one non-null immutable
  actor mapping.
- Collision reports and membership-backed list/user-info persist audit records
  before any global PII query. Audit failure is fail-closed and no raw email is
  placed in logs, audit metadata or a partial report.
- `0003` stores merge source/canonical Identity UUIDs without unsafe FKs.
  `0004` first establishes `UNIQUE(users.User.identity_id)`, then adds and
  validates those and all other Identity FKs before changing Django state to
  `ForeignKey(to_field="identity_id")`.
- The durable append-only tenant registry is seeded from the complete legacy
  discovery union before contract and becomes the sole Task 4/14/17 inventory
  authority. Every new tenant IAM row has a deferrable registry reference.
- Migrations `0003`/`0004` have exact reverse operations and pass separate
  empty-DDL and populated backfill-aware PostgreSQL round trips using
  historical `Apps` and the isolated alias. After a realized deferred merge,
  Task 2 plus `0003` is the fail-closed rollback floor. `0005` is explicitly
  irreversible and cannot begin while a legal hold exists or signed
  cutoff/backup evidence is absent.
- All required database FKs and tenant uniqueness constraints are present;
  `session_family_id` is unique, sessions snapshot Identity security version,
  only one unrevoked TOTP and one unfinished recovery token per purpose exist
  per Identity, and tenant policy state cannot be soft-deleted.
- Password verification creates only `MFA_PARTIAL` or
  `MFA_SETUP_PARTIAL`; setup confirmation returns ten one-time backup codes,
  and access/refresh/session/grant issuance happens only after MFA and tenant
  selection.
- Expired/abandoned MFA setup revokes its unconfirmed credential before a new
  challenge creates a fresh secret; confirmed credentials are never replaced
  by cleanup.
- Identity status is restricted to `PENDING_CREDENTIALS`,
  `MFA_SETUP_REQUIRED`, `ACTIVE` and `LOCKED`; status/factor contradictions fail
  closed.
- `TenantGrantSwitchRepository` receives signed expected current
  grant/tenant/context, uses one parameterized cursor on primary with
  ContextVar token restoration, bypasses router-backed tenant ORM, and never
  discovers the old tenant through an impossible RLS query.
- `TenantGrantSwitchOperation.operation_id` plus request fingerprint and the
  ten-minute encrypted response envelope makes initial selection and switches
  idempotent at the HTTP token/cookie level, not only at the grant-row level.
- A five-minute `TENANT_SELECTION` token can reach the ten-minute replay window
  only through the narrow expired-token branch for an already-resulted
  operation created before `exp`; it can never start or resume new DB work.
- Redis refresh-family CAS has explicit `INITIALIZED`, `REPLAYED`, `ROTATED`,
  `REUSED` and `MISSING_UNEXPECTED` results. Only a matching initial
  `DB_COMMITTED` operation may initialize an absent family at generation zero.
- Completed result replay revalidates the non-revoked/unexpired session,
  Identity snapshot and exact result grant before decryption. Every session
  revoke cryptoshreds its live result envelopes.
- DB rollback and post-commit Redis failure both revoke the session and persist reconciliation in a new transaction.
- The retention-queue reconciler processes every durable failure with bounded
  ID discovery, globally ordered graph locks, a non-blocking work advisory,
  retry/backoff/DLQ, deletes orphaned Redis families, leaves the session
  revoked and never restores access. It closes an incomplete switch only for
  `TENANT_SWITCH`; refresh/cleanup records have no switch FK or lookup.
- Expired open grants are superseded before a new grant is inserted.
- The database rejects `TenantSessionGrant.is_deleted=True`; authorization
  lifecycle uses only supersede, revoke and expiry, and an append-only trigger
  rejects rewrites of grant session, membership, tenant, context or issuance
  fields.
- `revoke_all_sessions()` never changes Identity version; one
  `invalidate_identity_security_context()` call owns each exact `N -> N+1`
  transition plus all-session revoke for logout-all and credential/MFA changes.
- Task 4 preflight, Task 14 stabilization and Task 17 pre-drop gate use the
  same `DurableIamTenantInventoryProvider`, enter every registry tenant
  separately under RLS and aggregate cross-tenant open-grant/session integrity
  without an unscoped manager.
- Legal hold blocks every IAM state mutation, including security response and
  retention; tests prove typed 403 and atomic unchanged state.
- Merge legal-hold observation and signed requeue/result are separate
  append-only evidence; neither held operation nor held subject changes, and
  hold release never resumes work automatically.
- Every merge-graph writer follows the same globally sorted advisory/User/
  operation/security/tenant lock order. Concurrent A→B/B→A, A→C/B→C,
  password-reset/source-add and finalizer/hold races serialize or return typed
  retry/conflict without a database deadlock.
- Deferred merge execution requires both the feature flag and a valid,
  unexpired Ed25519 coverage attestation for the exact runtime entry-point
  manifest and deployed build; stale, wrong-key, wrong-build or incomplete
  evidence keeps execution fail-closed.
- Login/MFA/recovery rate limits, recovery anti-enumeration, Origin/CSRF, cookie attributes, production secret checks and gateway header stripping are covered by tests.
- Kong has an anchored exact selection route, a context-free auth allowlist
  and a protected access-JWT catch-all. Expired selection reaches only exact
  POST without context headers; expired access is rejected on protected APIs.
- One exhaustive `AuthFailureCode` policy distinguishes 401/403/409/422/429/
  503, and temporary 429/503 responses expose the required `Retry-After`.
- Password and MFA recovery create purpose-bound durable operations plus
  encrypted owner delivery; APIs/outbox/audit/logs contain no raw recovery
  secrets, previous open same-purpose tokens are revoked, competing delivery
  workers have one CAS claimant and reuse identical material. Every token has
  an immutable exact operation link; consume completes only that operation,
  and the sensitive delivery task never forwards provider exceptions or
  tracebacks to the generic DLQ. MFA recovery revokes factors/sessions then
  forces fresh setup.
- Auth audit and required Transactional Outbox events are emitted without raw
  email/token/MFA data. The database rejects audit payload update/delete and
  permits only the explicit monotonic legal-hold latch.
- Mandatory-MFA reset emits canonical `identity.mfa_reset`, never
  `identity.mfa_disabled`; legacy `user.mfa_disabled` is conditional on a
  signed consumer inventory.
- Events in the approved compatibility map emit correlated `user.*` aliases
  for exactly one migration window; Task 16 removes them after consumer
  inventory is empty.
- During migration JWT contains stable `user_id`, plus `legacy_user_pk`,
  `identity_id` and `sub`; the first two identify the selected membership's
  tenant-local actor while the latter two identify the canonical Identity.
  Task 16 removes temporary `legacy_user_pk` and `username` only after the
  signed pre-cutoff consumer gate while preserving `user_id` through the
  membership actor mapping. Every identity-v1 token validates
  `iss="identity-v1"`/`aud="complect-api"`.
- Backend cross-validates every Identity/session/membership/grant/tenant claim
  before permissions; gateway validates the database-free identity subset.
- Common JWT authentication imports only the common validation Protocol/DTO;
  the users validator is injected once by `config.api` before router imports,
  and boundary tests/lint prevent a common-to-users domain import.
- Legacy `username` and new `email` login inputs remain compatible; conflicting
  dual input returns `422 AMBIGUOUS_IDENTITY_INPUT`.
- Typed version failures distinguish session revoke, disabled membership, stale authz/policy and tenant switch.
- Individual session revoke, logout-all and refresh reuse behavior are implemented.
- Parallel refresh, tenant switch, recovery-token consume and backup-code use
  tests prove single-winner CAS behavior.
- `IAM_IDENTITY_AUTH_MODE` enables issuer and validator atomically; no production `LOG_ONLY` fallback exists.
- Rollback uses the tested `revoke_identity_v1_sessions` command. Deprecated
  v1 refresh-body compatibility records token-free telemetry and remains
  available; cookie-only transport requires a future `/api/v2` migration.
- DB stabilization checks only durable DB invariants. Gateway, Prometheus,
  OpenAPI/SDK, browser and migration-lint evidence live in the separate release
  runbook; shadow coverage is complete eligible/comparison pairing with zero
  mismatches, not an arbitrary 10,000-event threshold.
- IAM-specific OpenAPI, generated SDK, frontend typecheck, Docker backend and
  gateway runtime checks pass.
- Repository migration lint remains a known unrelated Requests failure; merge
  readiness stays blocked until owner fix or a formal time-bounded waiver.
- Release N+3 removes `User.tenant_id`, legacy tenant indexes, plaintext MFA
  fields, global device fingerprint, legacy issuer/refresh storage and event
  aliases only after the signed seven-day retirement gate; it preserves the
  frozen v1 refresh body input. User
  listing/admin/user-info paths are membership-backed and tenant-isolated.
- N+3b is blocked until every global and tenant IAM hold count is zero; Plan 1
  never deletes held legacy evidence and does not invent an implicit legal
  archive. Migration `0005` additionally takes the exclusive frozen retirement
  barrier as its first SQL and repeats the complete global plus durable-
  registry/RLS tenant hold scan before DDL; every hold latch takes the matching
  shared barrier in a database trigger.
- N+3 also requires a written frontend/generated-SDK/external-integration JWT
  consumer inventory, owner approvals and browser acceptance without
  `username` or `legacy_user_pk`; server metrics alone are insufficient.
- Active deferred finalization requires owner convergence, usable canonical
  password and confirmed canonical MFA. Currently inactive
  `LOCKED`/`PENDING_CREDENTIALS`/`MFA_SETUP_REQUIRED` graphs instead require
  complete fail-closed artifact drain and do not compare password/MFA shadows,
  regardless of their earlier convergence mode.
- Plan 1 is complete only after Task 17 production acceptance; before that the
  system remains in a compatibility phase and IAM-EMPLOYEES does not start.

## Rollback

- Before the first `APPROVED_DEFERRED` operation, Release N may roll back
  through `0003 -> 0002` using the tested reverse path. After that first merge,
  the minimum rollback floor is the Task 2 merge-aware guard plus schema
  `0003`; startup and reverse migration refuse anything older.
- Before the Task 16 cryptographic cutoff, set the last **merge-aware**
  `IAM_IDENTITY_AUTH_MODE=LEGACY` artifact, close new identity-session endpoints and run
  `python manage.py revoke_identity_v1_sessions --reason rollback`.
- Keep expand tables and columns; do not drop data during rollback.
- Stop before Task 4 when collision/backfill preflight is not clean.
- On elevated switch reconciliation, close tenant select, globally revoke affected sessions and resolve durable reconciliation records.
- Never accept stale security versions and never switch the validator to `LOG_ONLY`.
- After Task 16, rollback is identity-only and fail-closed: never re-enable the
  legacy issuer/validator, source-alias login, removed claims/event aliases or
  purged refresh families.
- After Task 17/`0005`, ordinary schema rollback is unavailable. Restore only a
  known-good identity-only application compatible with `0005`; whole-system
  backup restore is a separately authorized disaster-recovery operation.

## Execution Handoff

Plan revised and saved to `docs/superpowers/plans/2026-07-30-iam-identity.md`.

1. Subagent-Driven — execute one task at a time with review gates and stop at each production release boundary.
2. Inline Execution — use `superpowers:executing-plans`, execute task-by-task
   and stop after Task 2 (Release N), Task 3 (Release N+1), Task 13 (Release
   N+2), Task 14 (Stabilization), Task 15 initial N+3a compatibility deploy,
   Task 16 signed JWT cutoff/finalization, and Task 17 N+3b for separate manual production
   approvals.
