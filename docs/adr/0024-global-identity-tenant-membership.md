# 0024 — Global Identity and Tenant Membership

## 1. Статус

| Поле       | Значение                                      |
| ---------- | --------------------------------------------- |
| Статус     | APPROVED                                      |
| Дата       | 2026-07-30                                    |
| Владелец   | Core Platform Team                            |
| Заменяет   | ADR-0006 частично: JWT payload и session model |
| Заменён на | -                                             |

---

## 2. Контекст

Текущая модель `users.User` совмещает глобальную личность, tenant-контекст,
MFA и часть session metadata в одной Django-модели. Это работает для одного
tenant на пользователя, но не выдерживает долгосрочную multi-tenant модель:
один человек может работать в нескольких tenant под одним email, а роли,
статус сотрудника и административные действия должны оставаться tenant-local.

Модуль IAM должен поддержать полный жизненный цикл сотрудника:
создание администратором, приглашение, установка пароля, MFA, вход,
переключение tenant, назначение ролей, блокировку доступа и немедленный отзыв
активных tenant-доступов. При этом глобальные security-события, такие как
смена пароля, компрометация refresh token или global logout-all, не должны
случайно зависеть от `tenant_id`.

Дополнительные ограничения:

- миграция выполняется по Expand / Backfill / Contract без downtime;
- одинаковые email между tenant не объединяются автоматически;
- tenant-admin не должен видеть или менять memberships пользователя в других
  tenant;
- браузерная сессия имеет ровно один активный tenant-контекст;
- старые вкладки и повторные refresh/switch операции должны fail-closed;
- RLS и `BaseEnterpriseModel` применяются только к tenant-scoped моделям.

---

## 3. Решение

Ввести разделение между логической глобальной `Identity` и tenant-local
`TenantMembership`.

`Identity` отвечает за email, credentials, MFA и глобальные security-сессии.
`TenantMembership` отвечает за принадлежность к tenant, статус сотрудника,
должность, роли и tenant-local administrative lifecycle.

### Границы Django User и Identity

Физическая стратегия: существующий `users.User` эволюционно становится
таблицей глобальной Identity.

`AUTH_USER_MODEL` остается `users.User`. Integer primary key сохраняется как
Django compatibility implementation detail для существующих FK, admin/auth
интеграций и миграционной совместимости. Целевой стабильный идентификатор
Identity - обязательный `identity_id UUID UNIQUE`, который используется в JWT,
новых IAM-моделях, audit-событиях и публичных ссылках между IAM-таблицами.

Password hash остается в `users.User.password`. Django authentication backend
аутентифицирует `users.User` как Identity по нормализованному email и password
hash. Бизнес-авторизация не должна читать `tenant_id` из Django User.
Tenant-контекст берется из активного `TenantSessionGrant`, затем попадает в JWT
и gateway headers.

Существующие доменные FK на `AUTH_USER_MODEL` продолжают ссылаться на
`users.User` integer PK до отдельной доменной миграции. Plan 1 IAM-IDENTITY не
должен массово переписывать 300+ доменных ссылок на пользователей.

Целевой инвариант:

- `users.User` как Identity глобален и не использует `tenant_id` как authority;
- `users.User.identity_id` является стабильным UUID-идентификатором Identity;
- `TenantMembership` tenant-scoped, содержит ссылку на canonical `identity_id`
  и неизменяемую ссылку на исходный tenant-local actor `users.User`;
- один `Identity` может иметь несколько active или inactive memberships;
- в одной браузерной `IdentitySession` активен не более один tenant grant;
- все protected API продолжают получать `tenant_id` только из проверенного JWT,
  не из body/query params.

### Модели

Глобальные модели без `BaseEnterpriseModel` и без RLS:

- `IamTenantRegistryEntry`: append-only infrastructure inventory with only
  `tenant_id UUID PRIMARY KEY`, registration timestamp/version and a closed
  non-PII source code. It is not tenant-owned data or authorization authority;
  `tenant_id` is the inventoried subject key. The registry exists solely to
  enumerate every tenant before entering FORCE-RLS IAM tables.
- `users.User` as Identity: integer Django PK, `identity_id UUID UNIQUE`,
  normalized email, original email, password hash, closed security status
  (`PENDING_CREDENTIALS`, `MFA_SETUP_REQUIRED`, `ACTIVE`, `LOCKED`),
  `security_version`, `email_normalization_version`, timestamps, legal
  retention fields.
- `IdentitySession`: `identity_id`, `session_family_id`,
  `security_version_snapshot`, device metadata, refresh token family metadata,
  `revoked_at`, `expires_at`; references `users.User.identity_id`, not the
  Django integer PK. `session_family_id` is globally unique because it is the
  Redis refresh-family identifier, not only a searchable attribute.
- `MfaCredential`: `identity_id`, credential type, encrypted secret,
  `key_id`, confirmation state, timestamps; references
  `users.User.identity_id`. A partial unique constraint permits at most one
  unrevoked TOTP credential per Identity.
- `BackupCode`: `identity_id`, code hash, `key_id`, used state; references
  `users.User.identity_id`.
- `RecoveryToken`: `identity_id`, token hash, closed purpose
  (`PASSWORD_RESET`, `MFA_RESET`), TTL, consumed/revoked state; references
  `users.User.identity_id` and has an immutable OneToOne `operation_id` to the
  exact `RecoveryOperation`. A partial unique constraint allows at most one
  unconsumed/unrevoked token per `(identity_id, purpose)`; an expired prior
  token is explicitly revoked before replacement.
- `RecoveryOperation`: durable password- or MFA-recovery state plus an
  encrypted one-time delivery envelope. The envelope stores ciphertext, nonce
  and `key_id`; Identity, purpose and expiry are derived only through the
  linked token, never duplicated as a second authority. The ordinary outbox
  payload contains only the operation ID and non-secret routing metadata.
  Delivery is a CAS state machine
  `PENDING_DELIVERY -> DELIVERING -> DELIVERED -> COMPLETED`, with closed
  `SUPERSEDED`, `EXPIRED` and `FAILED` terminal branches, a unique lease/attempt
  ID and an immutable provider Message-ID so competing wakeups cannot
  independently send or regenerate material. A deferred database integrity
  gate rejects deliverable orphan operations or token-operation rebinding.
- `PreAuthChallenge`: one-time password-verified state for MFA verification,
  MFA setup or merge convergence, with a five-minute TTL, explicit flow kind,
  attempt counters, `security_version` snapshot and consumed/revoked
  timestamps. A merge-convergence challenge additionally binds one merge
  operation/version, canonical security-version snapshot and idempotency key;
  those fields must be null for ordinary MFA flows. It cannot create an
  `IdentitySession` or tenant grant by itself.
- `TenantGrantSwitchOperation`: durable, globally unique `operation_id` plus
  request fingerprint, expected current grant/tenant/context, target tenant,
  expected/result refresh generation and digests, result state, short-lived
  AES-GCM response envelope and terminal failure state. The envelope contains
  the exact access/refresh/CSRF result so an HTTP
  replay returns the same cryptographic response rather than rotating again.
  This is the idempotency authority for initial tenant selection and later
  tenant switches.
- `SessionReconciliationRecord`: durable recovery payload for failed
  session/grant/Redis transitions with closed source kind
  `REFRESH_ROTATION`, `TENANT_SWITCH` or `SESSION_FAMILY_CLEANUP`, unique per
  `(session_id, kind, operation_id)`. Only `TENANT_SWITCH` has a nullable
  OneToOne reference to the exact external switch `operation_id`; a database
  integrity gate requires that operation to belong to the same session.
- `IdentityMergeOperation`: durable, versioned collision-resolution authority
  with the existing source `users.User` integer-PK FK, source and canonical
  Identity UUIDs, signed approval digest/reference, immutable reserved
  synthetic normalized email, immutable `approval_expires_at`, convergence
  mode, `credential_epoch` and result timestamps. Its closed states are
  `APPROVED_PENDING_CONVERGENCE`, `APPROVED_DEFERRED`,
  `READY_TO_FINALIZE`, `FINALIZED`, `CANCELLED` and `EXPIRED`.
  `CANCELLED` and `EXPIRED` are terminal pre-convergence outcomes: neither may
  transition back or change membership, email, credentials or security
  authority. Expiry is supported only while pending and is proved by the
  immutable approval deadline. A partial source constraint permits at most one
  operation whose state is pending or already realized
  (`APPROVED_PENDING_CONVERGENCE`, `APPROVED_DEFERRED`,
  `READY_TO_FINALIZE`, `FINALIZED`); `CANCELLED`/`EXPIRED` evidence remains
  append-only without preventing a later separately signed operation. The
  deterministic synthetic-email reservation uses the same partial predicate,
  so a terminal pre-convergence operation releases it without deleting
  evidence. Database checks reject `source_identity_id =
  canonical_identity_id`, permit at most one pending convergence per canonical
  Identity and enforce both partial reservations. There is deliberately no
  `BLOCKED_BY_LEGAL_HOLD` state.
- `IdentityMergeHoldBlock`: append-only observation that a merge operation or
  one of its connected source, canonical, membership, session or grant
  subjects is held. It records only the subject reference, hold digest,
  operation-version snapshot and observation time; it never changes the held
  aggregate.
- `IdentityMergeRequeue`: append-only, externally signed request bound to one
  exact hold observation/action digest and expected operation version. Its
  purpose-separated envelope also binds environment, deployed build, schema
  version, fresh action digest, nonce, issue/expiry and key ID. It does not
  itself resume work. `IdentityMergeRequeueResult` is the immutable OneToOne result
  evidence for the worker's fresh hold/version evaluation, with a closed
  outcome such as `APPLIED`, `HOLD_STILL_ACTIVE`, `VERSION_CONFLICT` or
  `INVALID_RELEASE_EVIDENCE`.
- `IdentitySecurityAuditRecord`: global append-only auth/security audit record
  without a fabricated tenant. It stores explicit actor type/reference,
  closed reason, request/correlation IDs, only pseudonymized subjects and
  non-secret metadata. A DB trigger rejects update/delete; its sole explicit
  mutation exception is `is_legal_hold FALSE -> TRUE` with every payload
  column unchanged.

Tenant-scoped foundation delivered by Plan 1 IAM-IDENTITY uses
`BaseEnterpriseModel`, tenant filtering, RLS and soft delete, except for the
security lifecycle rules below:

- `TenantMembership`: `tenant_id`, `identity_id`, employee profile fields,
  closed status (`DRAFT`, `INVITED`, `ACTIVE`, `DISABLED`), `authz_version`
  and immutable `legacy_actor_user` mapping to the integer PK of the existing
  `users.User` row that represented this person in that tenant. Canonical
  Identity may own memberships originating from several compatibility rows,
  while every membership retains exactly one source actor compatibility
  alias/tombstone for tenant-local historical actor references.
- `TenantSessionGrant`: `tenant_id`, `session_id`, `membership_id`,
  `context_version`, issued/expires/superseded/revoked timestamps.
- `TenantAuthorizationState`: tenant-wide `policy_version`.

The following target tenant-scoped models are explicitly outside Plan 1:

- `InviteToken` (`tenant_id`, `membership_id`, token hash, TTL,
  consumed/revoked state and `version`) belongs to Plan 2 IAM-EMPLOYEES;
- `TenantRole` and `MembershipRoleBinding` belong to the later ADR and Plan 3
  IAM-RBAC. Until that migration, current `Role`/`RoleBinding` remains the
  compatibility authorization authority;
- `ObjectAssignment` belongs to the later Assigned-resource Authorization ADR
  and Plan 4 AUTHZ-SCOPE.

Their boundary/lifecycle rules below are forward constraints, not a claim
that ADR-0024 or IAM-IDENTITY creates those tables.

`InviteToken` is tenant-scoped because invitation is an operation on a specific
membership. Tenant admins may revoke or resend invitations only inside their
tenant.

`InviteToken` must not use ordinary delete or generic soft delete for security
decisions:

- allowed transitions are `consume()` and `revoke()` through CAS;
- `expires_at` controls expiry;
- `consumed_at` controls one-time use;
- `revoked_at` controls administrative revocation;
- `version` controls concurrent invite operations;
- ordinary `.delete()` and `is_deleted=True` soft delete are forbidden;
- security lookup must use a dedicated manager/query path that does not hide
  rows by `is_deleted`;
- retention cleanup is the only path allowed to physically purge old invite
  records, subject to legal hold.

`InviteToken` does not need the same full append-only event history as
`TenantSessionGrant`, but generic repository deletion must not be able to change
whether an invitation is accepted.

`TenantAuthorizationState` also rejects `is_deleted = TRUE` with a database
check. Policy state is mutated only through explicit versioned operations; a
generic soft delete must not hide the only authorization authority for a
tenant.

### Invite selector and verifier

Invite links use a split selector/verifier format. The selector is signed and
routes the request; the verifier is the bearer secret.

Selector payload:

- `tenant_id`;
- `invite_id` or `membership_id`;
- `key_id`;
- `issued_at`;
- `signature`.

Verifier:

- random high-entropy secret generated per invite issue;
- sent only in the invite URL;
- never logged;
- never stored in plaintext;
- stored in DB only as `verifier_hash`.

Verification order:

1. Parse selector and verify `signature` using `key_id`.
2. Establish tenant context from signed `tenant_id`.
3. Perform tenant-scoped lookup of `InviteToken`.
4. Check `expires_at`, `consumed_at`, `revoked_at` and `version`.
5. Compare verifier against `verifier_hash` using constant-time comparison.

The signed selector must not contain the full bearer token.

### Tenant-free CAS contract

Global security models use a separate repository contract:

- stable UUID identity identifier;
- existing `users.User` keeps its integer primary key as Django compatibility
  detail;
- new global security models use UUID primary keys;
- `version` based optimistic concurrency control;
- explicit `updated_at`;
- legal retention / legal hold checks;
- no dummy `tenant_id`;
- no direct `.save()` or `.delete()` in command-side code.

The contract is implemented through focused repositories for Identity
security, pre-auth challenges, sessions, MFA/recovery and reconciliation, not a
generic `atomic_update(model_class, ...)` entry point. The project-wide legal
hold invariant has no IAM exception: `identity_legal_hold=True` or
`is_legal_hold=True` blocks every mutation, including Identity status/version,
session/grant lifecycle, MFA recovery, envelope cryptoshredding and retention.
The attempted command fails atomically with typed 403 and leaves all held
state unchanged. Release of a hold is a separately authorized legal workflow.

Merge processing does not encode a hold by mutating
`IdentityMergeOperation`. If the operation itself, a source or canonical
Identity, or any connected security/tenant row is held, no password, status,
version, email, membership, session, token, MFA or operation-state field in
that graph may change. The processor may only append a separate
`IdentityMergeHoldBlock`. A later signed `IdentityMergeRequeue` is processed
under a complete fresh lock-and-hold check and receives one immutable result;
release or mere absence of a hold never auto-resumes convergence or
finalization.

Plan 1 does not create a legal archive and therefore cannot move held legacy
columns elsewhere to make destructive retirement appear safe. Release N+3b
is blocked while any global or tenant IAM legal hold exists, even when the
columns appear unused and every other retirement metric is green.

Legal-hold creation and irreversible retirement share a database-enforced
barrier. Every `FALSE/NULL -> TRUE` IAM hold latch (including an inserted held
row) executes a trigger that first takes the transaction-scoped **shared**
advisory `IAM_RETIREMENT_BARRIER`. Its frozen signed-bigint key is
`0x49414D5F52455449`; runtime helpers and historical migrations must embed the
same value. Task 17 migration `0005` takes the matching **exclusive** advisory
lock as its first SQL statement, locks the frozen catalog of global/tenant
hold-bearing tables in stable name order with `SHARE ROW EXCLUSIVE`, and
rechecks all global holds plus every tenant-scoped hold through UUID-ordered
`IamTenantRegistryEntry` RLS contexts in that same transaction before any DDL.
Therefore a hold commits entirely before the
migration and blocks it, or waits until lawful DDL has committed; a fresh
external gate is evidence, not the concurrency barrier. Schema tests require
the trigger on every IAM table/column that can latch a hold and require the
runtime/migration barrier keys to match, so a future hold-bearing model cannot
silently bypass this protocol.

Tenant-scoped models continue to use the existing tenant-aware repository
contract. `TenantSessionGrant` is an exception to ordinary soft delete:
authorization decisions must use only explicit security states.

### RLS-safe IAM tenant inventory

Global preflight/stabilization commands cannot query FORCE-RLS IAM tables
without a tenant context and must not gain a generic unscoped manager. They
depend on one narrow contract:

```python
class IamTenantInventoryProvider(Protocol):
    def iter_tenant_ids(self) -> Iterator[UUID]: ...
```

Plan 1 creates `IamTenantRegistryEntry` in the expand migration because later
gates cannot safely depend on legacy `User.tenant_id`. Task 3 seeds it from a
one-time compatibility discovery union of legacy `User.tenant_id`,
`RoleBinding.tenant_id` and expected/target/result tenant IDs in global switch
operations. That discovery component is migration-only and is not used as the
post-contract inventory authority.

Every membership/authorization-state/grant/switch provisioning path calls
`ensure_registered(tenant_id)` before its tenant write in the same transaction.
The operation inserts idempotently and then locks the exact registry row.
Multi-tenant writers sort UUIDs and complete every registry ensure/lock before
their first RLS-table lock; registry and tenant ranks may not be interleaved.
Deferrable database FKs from Plan 1 tenant IAM rows and switch tenant
references to the registry prevent a row from committing for an uninventoried
tenant. Registry entries are append-only and are not removed when a tenant has
no current memberships. Task 4 blocks contract deployment if the compatibility
union contains an ID absent from the registry or any scoped IAM row lacks its
registry reference.

Task 4 preflight, Task 14 stabilization and Task 17's final pre-drop gate use
only `DurableIamTenantInventoryProvider`, which yields registry IDs in UUID
order. Each command enters one returned tenant's ContextVar and database RLS
context, filters every query by that tenant, checks membership/authorization-
state/grant integrity, restores context, then continues. Cross-tenant counts
are aggregated only from non-secret IDs/counters after those scoped reads.
The registry is a narrow inventory, never a generic unscoped reader for tenant
rows and never permission/tenant-existence authority for request handling.

### Email normalization and reconciliation

Email uniqueness is enforced only after a manual reconciliation gate.
Normalization version 1 is exact: trim surrounding whitespace, apply Unicode
NFKC normalization, casefold, validate email syntax and enforce at most 254
characters. It does not remove dots or plus aliases.

Rollout order:

1. Add nullable, non-unique `identity_id`, `normalized_email` and
   `email_normalization_version` expand columns.
2. Calculate normalization candidates from current email authority without
   requiring the candidate column to be unique.
3. Run a dry-run management command that reports duplicate candidates across
   existing tenants. Invalid or missing email/username candidates are recorded
   in a separate report section and do not abort scanning the remaining rows.
4. Store the reconciliation report as a protected PII artifact outside git and
   outside application logs. Its pre-provisioned directory is owner-only and
   symlink-free; the command rejects symlink/non-regular targets and writes by
   owner-only `0600` exclusive temp file, fsync and atomic rename.
5. Manually classify each duplicate as one `Identity` with multiple
   memberships or as separate people requiring corrected email data.
6. Apply the approved decision through a typed management command, never
   production SQL. `resolve_identity_collision --merge` and
   `resolve_identity_collision --correct-email` both support dry-run, use CAS,
   require an externally signed protected approval bound to the exact action
   digest, and append a pseudonymized security audit record.
   As an additional fail-closed eligibility guard, `--correct-email` rejects a
   target that participates as either source or canonical Identity in a live
   `APPROVED_PENDING_CONVERGENCE` operation using `MERGE_OPERATION_ACTIVE`;
   the operator must first execute a separately
   signed `--cancel-merge OPERATION_ID` and then obtain and execute a new,
   separately signed `--correct-email` action. Correction never implicitly
   cancels or reuses the approval for a merge operation. Absence of a pending
   operation is necessary but not sufficient: all other correction guards
   still apply, and a realized deferred/ready/finalized source does not become
   correctable merely because it is no longer pending. The signed correction
   envelope binds the target's expected `security_version` and a canonical
   digest of every locked merge-operation ID/state/version in which that
   target participates. The command recomputes the digest after acquiring the
   shared merge locks, so a pre-cancellation correction signature is rejected
   as `APPROVAL_CONTEXT_STALE` after cancellation.
   `--merge` does **not** merge on approval: it inserts a durable, versioned
   `IdentityMergeOperation(APPROVED_PENDING_CONVERGENCE)` with immutable
   approval/identity payload and changes no
   membership, email/normalized-email, password, MFA, security status/version,
   session, refresh family, challenge, recovery or token authority. A pending
   operation remains an unresolved normalized-email collision and blocks the
   contract gate. It may only enter deferred convergence or the terminal
   pre-convergence `CANCELLED`/`EXPIRED` states; cancellation and expiry do not
   perform any merge side effect or themselves block a later separately signed
   resolution of the still-existing duplicate.

   ```text
   APPROVED_PENDING_CONVERGENCE
       |-> CANCELLED
       |-> EXPIRED
       `-> APPROVED_DEFERRED
               `-> READY_TO_FINALIZE
                       `-> FINALIZED
   ```

   `CANCELLED`, `EXPIRED` and `FINALIZED` are terminal; no transition returns
   to a prior state. `CANCELLED` requires a fresh purpose-bound signed action
   for the exact pending operation/version. `EXPIRED` is an idempotent
   retention-worker CAS only after immutable `approval_expires_at`; both take
   the complete graph/hold locks, change no authority field and release only
   the partial source/synthetic reservation.
7. Complete a pending operation only through one of two closed convergence
   modes. `OWNER_CONFIRMED` is required for any authenticating/active graph.
   The exact source owner must authenticate, prove an existing confirmed or
   newly bound encrypted canonical MFA factor and choose one new valid
   password. That password is encoded once; the identical encoded value is
   written to the canonical row and every connected non-held source
   compatibility shadow. After a complete graph-level no-hold check, the same
   transaction increments canonical `security_version` once and durably
   revokes all sessions, grants, partial challenges and recovery/token
   artifacts before ownership moves. Redis refresh-family cleanup runs only
   after commit; stale artifacts already fail the DB version/epoch guard, and
   cleanup failure creates durable fail-closed reconciliation.
   `DORMANT_FAIL_CLOSED` may omit password and MFA convergence only when the
   canonical and every source row have `is_active=False` and security status
   `LOCKED`, `PENDING_CREDENTIALS` or `MFA_SETUP_REQUIRED`, every affected
   membership is `DISABLED` or `DRAFT`, and every session, grant, refresh
   family, pre-auth challenge and recovery operation is absent, expired or
   durably revoked/closed. A mixed active/dormant graph or an unproved artifact
   rejects the operation without partial mutation.
8. After the selected convergence preconditions hold, one locked CAS
   transaction performs the only merge point:
   `APPROVED_PENDING_CONVERGENCE -> APPROVED_DEFERRED`. It moves each
   source-owned membership to the canonical `identity_id`, preserves the
   immutable `legacy_actor_user` and current tenant-local `RoleBinding`,
   replaces only source `normalized_email` with the reserved unique
   `merged+<source-identity-uuid>@identity.invalid`, makes the source row
   non-authenticating, retains raw `email`/`username` only as compatibility
   selectors, records convergence mode and timestamps, and sets
   `credential_epoch=1`. Historical business-domain FKs are never rewritten.
   The transition is rejected when tenant footprints overlap or an
   actor/membership mapping is missing or ambiguous.
9. From `APPROVED_DEFERRED` until the signed Task 16 cutoff, lookup by a source
   username resolves the canonical Identity before any password comparison, MFA/recovery decision,
   status or security-version check, device policy, session lookup, Redis
   refresh-family access or token acceptance. Only canonical fields are
   authority; source password/status/version/MFA values are compatibility
   shadows even when synchronized. An alias attempt also consumes the
   canonical Identity's rate-limit bucket. `READY_TO_FINALIZE` and then
   `FINALIZED` require explicit signed gate evidence and a fresh complete
   graph/hold recheck; neither state reassigns historical actors or deletes
   tombstones/evidence. Every later password change/recovery encodes once on
   canonical and copies that exact encoded value only as a source shadow;
   MFA, status and security-version authority changes occur on canonical, with
   source epochs/snapshots changed only as fail-closed compatibility guards.
   Task 16 then removes the raw source row from login/recovery lookup without
   rewriting its historical actor row. Source username is no longer accepted.
   Recovery selects only a canonical `normalized_email`: if historical source
   spelling normalizes to that same canonical value it may initiate exactly
   one canonical recovery, while a stale source-only address returns the
   generic accepted response with no artifact. Neither case revives source
   Identity authority.
   A graph converged through `DORMANT_FAIL_CLOSED` may not transition its
   canonical Identity or any membership to `ACTIVE` while the operation is
   `APPROVED_DEFERRED` or `READY_TO_FINALIZE`. MFA confirmation, any recovery
   transition that would activate, global unlock and tenant enable all use the
   same typed activation guard.
   The graph must first satisfy inactive readiness and finalize during the
   signed Task 16 cutoff; after `FINALIZED`, ordinary canonical onboarding may
   activate it without reviving a source alias. This prevents an immutable
   dormant convergence mode from becoming an unfinalizable active graph.
10. Backfill a distinct per-row `identity_id`, normalized email provenance,
   memberships with `legacy_actor_user_id` fixed to the originating User PK,
   and exactly one `TenantAuthorizationState(policy_version=1)` for every
   membership tenant in idempotent batches. Candidate discovery independently
   checks missing global fields and, per explicit RLS tenant, missing actor
   memberships; approval-time initialization of global fields cannot hide a
   User from membership backfill. If a deferred merge already
   created an actor mapping whose canonical `identity_id` differs from the
   source alias row, backfill recognizes the append-only operation proof and
   keeps that mapping; it never resurrects a source-Identity membership.
11. Add final `NOT NULL` and unique constraints only after unresolved
   duplicates are zero and the backfill is complete.

Automatic merge by email is forbidden.

Before either reconciliation decision, one shared foundation helper may fill
only missing expand metadata needed to identify the rows. Approval itself must
not change membership, email, credentials or security authority, and no
Release N flow increments a nullable `security_version` directly.

The `identity.invalid` domain is reserved exclusively for the synthetic
normalized value of internal source aliases. Ordinary account creation,
invitation, corrected-email and recovery delivery paths reject it; it is never
treated as a deliverable address. Raw source `email`/`username` remain through
the Task 16 compatibility cutoff, but are selectors/display shadows only and
never global email, recovery-delivery or credential authority. Reports may
exclude such a raw deferred source from collision grouping only after the
operation, source, canonical, synthetic normalized value and moved membership
form the exact relational proof; audit evidence alone is insufficient.

`email_normalization_version` is technical provenance for the value currently
stored in `normalized_email`. It is not part of the uniqueness key.

When normalization rules change:

1. Calculate `normalized_email_candidate` with the new algorithm.
2. Run reconciliation against candidate collisions.
3. Resolve collisions manually.
4. Replace `normalized_email` with the candidate value.
5. Keep `UNIQUE(normalized_email)` as the global invariant.

### Identity merge locking and runtime enablement

#### Audit Legal Hold identity fence (P0, 2026-09-10)

`SHARED_IDENTITY_FENCE_PLUS_DB_TRIGGER` is the normative contract for
identity-scoped audit Legal Hold and merge authority transitions. Both share
the existing **exclusive, transaction-scoped** `iam-identity` advisory fence;
"shared" means a common protocol, not a shared-mode identity lock. Its key is
the signed big-endian int64 represented by the first eight bytes of
`SHA256(UTF8("iam-identity:") || UUID network-order bytes)`. Python's
`identity_advisory_key()` and PostgreSQL's
`public.users_identity_fence_key(uuid)` implement that frozen contract.
The complete deduplicated identity set is ordered by UUID bytes, never by the
resulting signed advisory keys. No second identity namespace is introduced.

Conflicting successful transactions serialize at fence acquisition. A hold
that owns the fence commits before a waiting convergence reads its fresh hold
snapshot. A later hold does not preempt a transition already owning the fence;
the supported writer waits before its DML and installs the hold afterwards.

The internal supported writer `latch_identity_audit_hold` has no HTTP/admin
endpoint. A trusted caller must authorize the exact deduplicated audit-ID set
before entering a short dedicated `READ COMMITTED` transaction. Ambient
transactions are rejected. The order is: shared retirement barrier, complete
identity discovery, exclusive UUID-byte-ordered identity fences, session rows
ordered by ID, target audit rows ordered by ID, binding revalidation, then only
the monotonic `FALSE -> TRUE` latch. An already-held row is a no-op, with no
`TRUE -> TRUE` UPDATE. After identity fences the writer cannot start another
graph command or acquire User/bootstrap locks. Batches are bounded to 1,000
records; each of at most three whole attempts uses 2s lock and 5s statement
timeouts. Only binding rediscovery and SQLSTATE `55P03` retry; permanent
binding errors and statement timeout are returned to the caller.

Stored audit `identity_id` is authoritative, including historical source
identities; it is never replaced by a guessed canonical identity. Session-only
evidence resolves through `IdentitySession.id -> identity_id`. Resolve before
fences and revalidate under session row locks afterwards; a changed binding
rolls back the entire operation before retry, without extending the fence set.
Both references must agree. Missing or unresolved references fail closed and
require a separate legal/reconciliation workflow; immutable payload is not
rewritten and subject enums are not extended.

Migration `0003a_identity_audit_fence` adds separate AFTER INSERT/UPDATE
statement guards with transition tables. They guard held INSERT and precisely
`FALSE -> TRUE` UPDATE, resolve/deduplicate/order identities, and use only
`pg_try_advisory_xact_lock` after implicit DML locks. Session rechecks use
`FOR KEY SHARE NOWAIT`; contention raises `55P03` and rolls back the statement.
The audit table's existing retirement trigger is replaced with a try-shared
retirement precondition so it also cannot block in reverse order. Contract
violations use `23514`; unsupported isolation uses `25001`. Canonical `0003`'s
audit append-only trigger remains installed and unchanged.

Session ID and identity binding are immutable. Deleting a session referenced
by any audit record is forbidden, including unheld evidence that may later be
latched. Every new session-linked audit INSERT, even unheld, participates in
the same fence and NOWAIT session-binding check. It must reference an existing,
consistent session; this closes the race where uncommitted unheld evidence
was invisible to a concurrent session DELETE/reuse check. Legacy unresolved
unheld evidence may remain for reconciliation, but cannot be newly introduced
with a session reference. Creating a session with an already-referenced UUID is also forbidden;
the existing primary key prevents duplicate live UUIDs. This intentionally
retains the minimal session linkage without adding a tombstone table. Session
INSERT/DELETE guards use try-retirement/try-identity fences after DML row locks;
they never wait for an identity fence. Legacy orphan audit references cannot
be silently rebound by creating a replacement session. Expired/revoked session
state may still change without reassigning its identity.

All protected graph commands require `READ COMMITTED`; neither repeatable-read
nor serializable snapshots are accepted. Owner start/complete, dormant
convergence, requeue/expiry and cleanup call a fresh separate Legal Hold query
after complete graph fence acquisition and before authority CAS in the same
transaction. This query includes direct audit references and session-only
audit evidence. Audit history is not added to convergence's row-lock ranks.
Redis I/O placement is unchanged by this decision.

Functions are migration-owned, schema-qualified, SECURITY INVOKER with fixed
`search_path=pg_catalog`. The deterministic key is IMMUTABLE/STRICT/PARALLEL
SAFE; trigger functions are VOLATILE. Runtime needs ordinary table privileges
and EXECUTE on the key function, not object ownership. Active guards and a
runtime role unable to disable, replace, ALTER/DROP or own them, transfer table
ownership, or set replication bypass are mandatory deployment gates. A local
admin connection or disposable restricted-role test is not production proof.

Deployment is coordinated: stop and drain all old convergence, requeue,
cleanup and credential/authority nodes; validate canonical `0003` schema and
resolve any independent schema drift; apply `0003a` with bounded DDL timeouts;
verify key vectors, enabled trigger/function metadata, existing held bindings
and production privileges; deploy all nodes with fresh audit-hold discovery;
only then re-enable authority processing and the supported writer. Mixed old
and new authority nodes are forbidden. The migration fails closed on existing
unresolved/conflicting held evidence. Forward DDL does not repair that data.

Application rollback must retain fresh audit discovery and this fence protocol,
or keep all affected authority processing disabled and drained. Reversing the
fence migration is permitted only under that same quiescence; its explicit
reverse restores the canonical audit retirement trigger and removes only
fence-owned objects. A pre-fix build must never resume authority mutations
merely because application or schema rollback succeeded.

Every approval, convergence, credential synchronization, finalization,
hold-release requeue and other merge-graph mutation uses one
`IdentityMergeLockCoordinator`; individual repositories may not invent a
local lock order. The coordinator operates on primary and follows this global
protocol:

1. Discover the complete connected participant graph from every operation edge
   except terminal pre-convergence `CANCELLED`/`EXPIRED`, source actor mappings
   and affected memberships. `FINALIZED` edges remain part of hold/integrity
   discovery even after their source alias stops authenticating. Gather all
   User PKs and non-null Identity UUIDs before requesting any graph lock.
2. Start one transaction and take transaction-scoped advisory locks first for
   sorted User-PK bootstrap keys, then for sorted Identity UUIDs. The bootstrap
   namespace is also used while `identity_id` is null.
3. Lock all participating `users.User` rows with `ORDER BY pk`, then all
   participating `IdentityMergeOperation` rows with `ORDER BY id`, followed by
   existing `IdentityMergeHoldBlock`, `IdentityMergeRequeue` and
   `IdentityMergeRequeueResult` evidence rows in that table-rank/PK order.
4. Lock remaining global rows using one closed table-rank registry and the
   total key `(table_rank, primary_key)`: `IamTenantRegistryEntry`,
   `MfaCredential`, `BackupCode`,
   `RecoveryOperation`, `RecoveryToken`, `PreAuthChallenge`,
   `IdentitySession`, `TenantGrantSwitchOperation`,
   `SessionReconciliationRecord`. Then, per sorted tenant UUID, use the exact tenant table
   rank `TenantAuthorizationState`, `TenantMembership`, `RoleBinding`,
   `TenantSessionGrant`, with rows ordered by primary key inside each table.
   Tenant rows are still discovered and accessed through explicit RLS tenant
   contexts; this ordering does not create an unscoped manager. A batch must
   materialize all tenant intents before the first tenant write; User-PK order
   may never implicitly choose tenant order.
5. Re-read the complete graph, every expected CAS version, actor/membership
   mapping and every legal-hold latch. Only then evaluate or mutate state.

If the locked re-read discovers a new participant, operation edge, version or
hold not represented by the gathered set, the whole transaction exits with a
typed retry and restarts discovery. It must never acquire an incremental
out-of-order lock. Tests must cover reversed caller input, connected
multi-source graphs, concurrent graph expansion, version conflicts and holds.
Every membership/actor/RoleBinding foundation writer takes the same bootstrap
participant key before tenant-row locks. Batched backfill uses try-advisory
selection and reports contention, but never `skip_locked` on a target User;
otherwise a phantom membership could evade the re-read.

Deferred merge execution has two independent runtime gates. The configured
flag `IAM_DEFERRED_MERGE_EXECUTION_ENABLED=true` is necessary but does not make
execution effective by itself. A coverage attestation is a canonical manifest
containing at least the environment, build commit, manifest version and digest
of the exact production entry points that can resolve an identifier or consume
credential/status/version/MFA/session/token authority, plus issuance/expiry
and signing key ID. It asserts that every listed path resolves a deferred
source alias to the canonical Identity before any authority decision. The
manifest is signed with an approved Ed25519 key and verified against the
deployed build and configured entry-point inventory.

The runtime boundary is a focused
`IdentityMergeCoverageAttestationVerifier` Protocol. Given the running build
commit, computed entry-point digest and current time, it returns an immutable
typed decision containing signature, expiry, build and manifest-match results;
callers cannot supply a boolean override or omit an entry-point class.

The effective flag is therefore:

```text
deferred_merge_execution_effective =
    IAM_DEFERRED_MERGE_EXECUTION_ENABLED
    AND signature_valid
    AND attestation_not_expired
    AND attested_build_commit == running_build_commit
    AND attested_entrypoint_digest == runtime_entrypoint_digest
```

Startup, the pending-to-deferred convergence CAS and every later finalization
CAS recompute this result.
Missing, stale, malformed, wrong-build or incomplete evidence makes the
effective value false and fails closed before graph mutation. Reporting,
signed pending approval, correction of a genuinely wrong email and hold
evidence remain available; the convergence and finalization transitions do
not.

### Session and grant model

`IdentitySession` is global. It has no direct FK to the active
`TenantSessionGrant`.

Grant lookup distinguishes current/open state from authorization-active state.

Open/current grant:

- `session_id = current IdentitySession`;
- `superseded_at IS NULL`;
- `revoked_at IS NULL`.

Authorization-active grant:

- open/current grant;
- `expires_at > now`.

Database constraint enforces at most one open/current grant:

```sql
UNIQUE (session_id)
WHERE superseded_at IS NULL
  AND revoked_at IS NULL;
```

An expired grant can still be open/current. It must fail authorization because
`expires_at <= now`, but it still occupies the partial unique constraint until
it is superseded or revoked.

`TenantSessionGrant` is append-only for security lifecycle purposes:

- allowed transitions are `supersede()` and `revoke()` through CAS;
- normal update/delete APIs must not mutate grants;
- `is_deleted` is not read by authorization policy;
- a database `CHECK (is_deleted = FALSE)` prevents generic soft delete from
  changing lookup visibility;
- a database `BEFORE UPDATE` trigger permits changes only to
  `superseded_at`, `revoked_at`, `version` and `updated_at`; session,
  membership, tenant, context version, issuance/expiry and all other authority
  fields are immutable after insert. Lifecycle timestamps can only move from
  null to a timestamp and cannot later be cleared or replaced;
- physical cleanup is performed only by a retention job;
- legal hold blocks retention purge.

Grant states are derived from timestamps:

- active: no `superseded_at`, no `revoked_at`, not expired;
- open expired: no `superseded_at`, no `revoked_at`, expired;
- superseded: `superseded_at` set;
- revoked: `revoked_at` set;
- expired: `expires_at <= now`.

Before creating any new grant for a session, the implementation must atomically
supersede any existing open/current grant for that `IdentitySession`, including
an expired grant.

Data integrity constraints are mandatory:

- one non-deleted `TenantMembership` per `(tenant_id, identity_id)` through a
  partial unique constraint;
- every `TenantMembership` has exactly one non-null, immutable
  `legacy_actor_user_id` FK to an existing `users.User` integer PK; only the
  locked transition to `APPROVED_DEFERRED` may change
  `membership.identity_id`, and it may never rewrite this actor mapping;
- one non-deleted membership per `(tenant_id, legacy_actor_user_id)` through a
  partial unique constraint, so tenant-local actor resolution is unambiguous;
- when the actor User's own `identity_id` differs from the membership's
  canonical `identity_id`, contract preflight requires one matching
  `IdentityMergeOperation` in `APPROVED_DEFERRED`, `READY_TO_FINALIZE` or
  `FINALIZED`, binding the exact source actor, source Identity and canonical
  Identity; an unexplained mismatch or merely pending approval blocks
  contract;
- exactly one `TenantAuthorizationState` per `tenant_id`;
- `TenantMembership.identity_id` and `IdentitySession.identity_id` reference
  `users.User.identity_id`;
- after Identity uniqueness exists, both Identity UUID references on
  `IdentityMergeOperation` reference `users.User.identity_id`; the existing
  source actor FK continues to reference the integer Django PK;
- `TenantSessionGrant.session_id` references `IdentitySession.id`;
- `TenantSessionGrant.membership_id` references `TenantMembership.id`.

The grant service additionally verifies
`grant.tenant_id == membership.tenant_id` and
`session.identity_id == membership.identity_id`. `TenantMembership` does not
duplicate the global email. An immutable delivery-address HMAC or invitation
snapshot may exist on an invitation record, but it is not a membership
authority.

### Audit-before-PII reads

Every IAM path that can reveal global email or employee PII follows one
general audit-before-read contract. This includes the collision-report command
and tenant-scoped user list, admin detail and user-info providers, in addition
to recovery delivery. Before the PII query begins, the caller commits a
durable append-only audit record with authenticated actor or system actor,
closed purpose, tenant when applicable, request/correlation IDs and only
pseudonymized subject or operation identifiers. The PII query runs only after
that audit commit succeeds.

Audit persistence failure is fail-closed: the command or request returns a
typed dependency failure and performs no PII query or read. Raw email, name,
delivery address and report contents are forbidden in audit fields and
metadata. Tests must prove ordering by making the audit write fail and
asserting that the PII query was never executed.

The request-scoped audit identity is carried by a domain-neutral frozen
`PiiReadContext` in `apps.common`. It contains only authenticated actor type
and pseudonymous actor reference, validated `tenant_id`, closed purpose,
`request_id` and `correlation_id`. The common JWT boundary may establish this
context only after `AuthContextValidationProtocol.validate()` has returned a
valid `AuthenticatedContext`; it must never derive the tenant or actor from
unvalidated headers or token claims. An explicitly named system scope is the
only non-request alternative and requires the same fields; there is no
anonymous or inferred default.

`ContextIsolationMiddleware` owns outer lifecycle cleanup. Its pre-request
hard reset clears stale PII context, and its `finally` hard reset clears it on
success, exception and cancellation. Context installation returns the native
`ContextVar` token and nested scopes restore that exact token. Every PII
provider calls `require_pii_read_context(expected_tenant_id=...)` before the
audit write; a missing context or tenant mismatch is a typed fail-closed error
and performs neither audit nor PII query. Tests cover A-to-B reuse of one ASGI
execution context, exception cleanup, concurrent A/B requests, missing
context, tenant mismatch and an explicit system scope.

### Tenant switching

`POST /api/v1/auth/tenant/select` (без завершающего `/`) switches the active tenant inside the current
identity session.

Rules:

- request requires an `Idempotency-Key` or internal `switch_operation_id`;
- replay of the same operation/fingerprint returns the already-created grant
  and the exact same access/refresh/CSRF result while the encrypted response
  envelope is live;
- `TENANT_SELECTION` expires after five minutes while the encrypted result
  envelope lives for ten. After token expiry it may authenticate only a replay
  of an existing `DB_COMMITTED`/`COMPLETED` operation created before `exp`,
  whose signature, issuer, audience, type, `jti`, identity claims, target and
  fingerprint still match. The fingerprint includes a keyed digest of the raw
  selection token. This branch cannot create an operation/session/grant,
  resume `PENDING` work or mint replacement tokens. A pre-existing pending
  intent may only be closed failed and its no-token session revoked;
- replay must not create a second grant;
- replay must not increment `context_version` twice;
- replay must not revoke a successfully switched session;
- switch operation supersedes any current open grant for the session,
  regardless of `expires_at`, and creates a new grant in one DB transaction;
- refresh token rotates in the same logical operation;
- old browser tabs receive `TENANT_SWITCHED` after backend version mismatch;
- frontend may use `BroadcastChannel`, but backend version checks are the
  source of truth.

The only canonical tenant-select path is exact
`POST /api/v1/auth/tenant/select`, without a trailing slash. Kong preserves
five route-scoped boundaries: the existing unauthenticated health/metrics/
public allowlist; context-free docs/OpenAPI; this highest-priority exact auth
selection route; an anchored allowlist of other context-free auth flows; and a
protected catch-all. Public/docs routes strip supplied Authorization and all
context headers and set only upstream gateway trust; they never fall through
to access-JWT enforcement. Every
policy strips spoofed trust/context headers and sets only gateway-owned trust
and client IP. The exact selection route preserves raw Authorization but does
not decode it or set Identity/tenant headers; the endpoint has `auth=None` and
its dedicated decoder owns both strict live-token validation and the narrow
expired-result replay branch. Other context-free endpoints accept only their
specific live partial/cookie flows, and the protected catch-all requires a
valid, unexpired `type=access` JWT before setting context. The same
`TENANT_SELECTION` token is rejected on every other method/path shape. This
does not weaken expiry validation on any other route or token type.

Before decrypting a persisted response, the backend revalidates on primary
that the session is not revoked or expired, its security snapshot still equals
the Identity version, and the exact recorded result grant still belongs to the
same session/membership/Identity and is the active recorded context. A
non-held failure cryptoshreds the envelope and returns the typed
revoke/version/switch error. A held row is never decrypted or replayed,
returns typed 403 and remains unchanged. Every successful session-revoke path
also cryptoshreds all live non-held response envelopes bound to that session
after its complete legal-hold precheck.

`IdentitySession` remains valid when tenant is switched. Only the previous
tenant grant is superseded.

The same open-grant supersede rule applies to:

- manual `POST /api/v1/auth/tenant/select`;
- auto-select tenant after login;
- context restoration or tenant re-selection during refresh.

No access token, refresh token, `IdentitySession` or `TenantSessionGrant` may be
issued after password verification alone. The required flow is:

1. Verify normalized email and password.
2. Create a five-minute `PreAuthChallenge`.
3. For `ACTIVE` Identity with a confirmed factor, return signed `MFA_PARTIAL`,
   verify TOTP or a one-time backup code, then consume the challenge.
4. For `MFA_SETUP_REQUIRED` Identity without a confirmed factor, return signed
   `MFA_SETUP_PARTIAL`. This token authorizes only MFA setup and confirmation.
   Confirmation generates ten one-time backup codes, moves the Identity to
   `ACTIVE` and consumes the challenge.
5. After either verified path, auto-select the only enabled membership, or
   return a five-minute tenant-selection token when several enabled
   memberships exist.
6. Only after tenant selection, create `IdentitySession` and
   `TenantSessionGrant`, then issue access and refresh tokens.

`PENDING_CREDENTIALS` and `LOCKED` identities cannot enter either partial flow.
An `ACTIVE` identity without a confirmed factor is an integrity failure and
fails closed.

MFA enrollment has an explicit abandoned-setup lifecycle. Before issuing a
fresh `MFA_SETUP` challenge, lock the Identity and any unconfirmed unrevoked
TOTP credential. An expired/revoked/consumed bound challenge causes CAS revoke
of that credential; a fresh password login supersedes any still-live older
setup challenge and revokes its pending credential. The same cleanup runs
before `setup_totp()` inserts a new credential. A confirmed credential is never
automatically replaced. Retention may later purge old revoked/unconfirmed rows
subject to legal hold, but revocation alone unblocks the unique constraint.

Current-context refresh and tenant-switch tokens contain signed:

- `session_id`;
- `current_grant_id`;
- `current_tenant_id`;
- `context_version`;
- token type, `key_id`, issued-at and expiry.

The first tenant selection after MFA uses null current grant and tenant values.
The signed values are routing inputs for the narrow repository; they are always
revalidated against locked DB rows and never trusted as authorization facts.

### RLS-safe grant switching repository

Tenant switching crosses two tenant contexts in one security operation. Generic
tenant-scoped repositories are not allowed to perform this by disabling RLS or
by exposing all-tenant ORM access.

Introduce a narrow `TenantGrantSwitchRepository` infrastructure contract.
The current `EnterpriseClusterRouter` injects RLS state from the request
`ContextVar` before ordinary ORM routing, so a manual `SET LOCAL` followed by
router-backed ORM is unsafe. The repository therefore obeys all of these
mechanical restrictions:

- every transaction is `transaction.atomic(using="default")`; replica access
  is forbidden;
- tenant-dependent grant and membership lock/update/insert operations use one
  cursor on `connections["default"]` and parameterized raw SQL;
- ORM access is limited to global tables and explicitly uses
  `.using("default")`, so it does not pass through router selection;
- a private context manager sets both the tenant `ContextVar` and
  `SET LOCAL app.current_tenant_id`, then restores the exact prior ContextVar
  token in `finally`;
- no public unscoped manager or reusable RLS-disable switch is introduced.

Within that boundary the operation is:

1. In a short global DB transaction, use the shared coordinator to lock the
   signed Identity/merge graph, all signed current/target
   `IamTenantRegistryEntry` rows in UUID order, and `PreAuthChallenge` before
   any existing `IdentitySession` or switch-operation row. An absent registry
   row fails typed and is never auto-created from request input. Then create or
   lock the session and insert a durable
   `TenantGrantSwitchOperation(status=pending)` in the closed global rank.
   Initial selection creates the session here, after MFA and target selection,
   before any token is issued.
2. Commit the operation intent so it survives a later grant-transition
   rollback.
3. Start the grant-transition DB transaction and use the shared coordinator to
   lock the global Identity/merge graph, all current/target registry rows,
   `IdentitySession` and switch operation in the closed global rank.
4. Materialize the signed current tenant, when present, and target tenant
   intents before the first tenant lock; sort their UUID bytes.
5. Enter each sorted tenant context once and lock, in the closed tenant rank,
   `TenantAuthorizationState`, the exact `TenantMembership`, relevant
   `RoleBinding` rows and then the exact current `TenantSessionGrant` when that
   tenant owns it. No grant is mutated yet.
6. After every current/target row is locked, re-read and validate the signed
   expected grant/tenant/context, membership Identity/status, policy versions,
   actor mapping and every hold. A changed graph restarts the complete
   transaction with typed retry.
7. CAS-supersede the existing open/current grant, including an expired grant,
   and only then create the target `TenantSessionGrant`.
8. Mint one access/refresh/CSRF response,
   store only its refresh digest in session state, and persist the exact
   response in a short-lived AES-256-GCM envelope together with the operation
   result and `status=db_committed` in the same transaction.
9. Commit the grant-transition DB transaction.
10. Rotate Redis refresh state with a Lua compare-and-swap operation.
11. Mark the switch operation complete.

The response envelope uses a dedicated Vault-backed current/previous keyring,
a fresh nonce and associated data bound to operation ID plus request
fingerprint. Its replay window is ten minutes. A same-fingerprint replay in
`db_committed` resumes the idempotent Redis CAS; a replay in `complete` returns
the identical decrypted response. A different fingerprint fails with
`IDEMPOTENCY_KEY_REUSED`. Once the envelope expires, replay fails with
`IDEMPOTENCY_RESULT_EXPIRED` and never rotates a replacement token. Missing or
corrupt material before Redis completion enters fail-closed reconciliation.
Retention cryptoshreds bearer ciphertext/nonce/key ID at expiry only when the
operation is not held. Expired material is never replay-eligible. Under legal
hold the encrypted envelope and referenced key remain preserved until the
separately authorized hold-release workflow permits cryptoshredding.

Redis refresh-family synchronization is one closed Lua state machine:

- `INITIALIZED`: the family key is absent and a locked initial
  `DB_COMMITTED` operation proves DB/result generation `0` and the same new
  digest, so the key is created;
- `REPLAYED`: key generation, digest and last operation ID already equal the
  result, so success returns without another increment;
- `ROTATED`: existing generation/digest equal the expected values and are
  atomically replaced;
- `REUSED`: an existing key matches neither expected nor exact replay state,
  so the affected global session family is revoked;
- `MISSING_UNEXPECTED`: the key is absent for ordinary refresh or a later
  switch, so the operation enters fail-closed reconciliation.

Only initial auto-select/manual-select and resumption of their proved
`DB_COMMITTED` operation may use `INITIALIZED`. Lua does not establish the DB
proof; the primary-DB adapter locks and validates it before calling the script.

The repository signature includes `session_id`, `expected_current_grant_id`,
`expected_current_tenant_id`, `expected_context_version`, `target_tenant_id`
and globally unique `operation_id`. It never attempts to discover the old
tenant by querying RLS-protected grants with only `session_id`.

DB exceptions are handled outside the rolled-back `transaction.atomic()`.
Redis failures are handled after the DB commit. In either case a separate new
DB transaction globally revokes the `IdentitySession`, records the terminal
switch-operation state and inserts `SessionReconciliationRecord`. Code must not
catch a database error and attempt to write reconciliation state inside a
broken transaction.

This repository is not a general cross-tenant data access API. It may access
only `IdentitySession`, `TenantSessionGrant`, `TenantMembership`,
`TenantGrantSwitchOperation` and required session reconciliation records for
the switch operation.

Plan 1 IAM-IDENTITY must include tests for:

- tenant A to tenant B;
- tenant B to tenant A;
- tenant A to tenant A idempotent replay;
- old open grant already expired;
- target membership disabled;
- failure after supersede and before insert creates reconciliation state and
  does not silently restore access.
- request `ContextVar` initially set to tenant A while the repository selects
  tenant B, proving the router cannot overwrite the repository's B context.
- concurrent HTTP requests with one `Idempotency-Key`;
- replay after a completed response, after `db_committed` but before Redis
  completion, and after loss of the first HTTP response; each observable
  success returns the same access/refresh/CSRF material.

### Refresh rotation and reuse detection

Refresh token rotation remains mandatory, but it becomes session-family aware:

- every refresh token belongs to an `IdentitySession.session_family_id`;
- refresh requires
  `IdentitySession.security_version_snapshot == Identity.security_version`;
- successful refresh rotates the token and preserves the current active grant;
- reuse of an old refresh token revokes the whole affected session family;
- global compromise handling increments `Identity.security_version`;
- reuse detection emits `auth.refresh_reuse_detected`.

Redis may cache refresh state, but the durable DB session/grant state is the
source needed for audit, reconciliation and logout-all.

### Security context versions

JWT must contain independent version claims:

- `identity_security_version`;
- `membership_authz_version`;
- `tenant_policy_version`;
- `grant_context_version`.

Version invalidation rules:

- password change, MFA change, recovery, global logout-all:
  increment `Identity.security_version`;
- membership status change or membership role binding change:
  increment `TenantMembership.authz_version`;
- tenant role permission matrix change:
  increment `TenantAuthorizationState.policy_version`;
- tenant switch:
  increments grant `context_version` and supersedes the previous grant.

Changing a role in tenant A must not invalidate active work in tenant B unless
the same identity session is currently using tenant A.

Global invalidation has one owner. `revoke_all_sessions(identity_id, reason)`
only revokes sessions and clears their replay envelopes; it never changes
`Identity.security_version`.
`invalidate_identity_security_context(identity_id,
expected_security_version, reason)` performs exactly one `N -> N+1` CAS plus
that same all-session revoke in one primary transaction. Logout-all, password
change/reset and MFA enable/reset use the latter operation. Individual
logout, refresh-family reuse response and tenant-local grant/membership revoke
do not increment the global Identity version. The approved Release N
email-reconciliation command is a migration-only exception before
IdentitySession authority is enabled; it performs its own audited CAS because
the runtime invalidation service does not yet exist.

### Typed auth failure HTTP semantics

Auth services raise one closed `AuthFailureCode`; call sites cannot supply an
arbitrary HTTP status. The users API boundary owns one exhaustive mapping:

- invalid credentials and invalid/expired auth context: `401`;
- Origin, CSRF and permission/membership denial: `403`;
- rate-limit threshold: `429` with the exact positive `Retry-After`;
- idempotency conflict or expired stored result: `409`;
- Redis limiter outage, refresh/switch reconciliation and primary auth/DB
  dependency outage: `503` with fixed `Retry-After: 3`;
- ambiguous dual login identity input: `422`.

Every enum member appears exactly once; there is no default `401` fallback for
typed failures. Generic Django/Ninja authentication and permission exceptions
retain their generic 401/403 envelopes. Recovery-start rate limiting remains
anti-enumeration `202` and does not expose a 429.

### Cryptographic key rotation

Encrypted MFA secrets, backup code selectors and signed token selectors must
store `key_id` or equivalent key version. Runtime verification must support
current and previous allowed keys during rotation.

Key rotation must not force all users to reset MFA.

### Password and MFA recovery

Recovery is owner-confirmed and remains global. Public start endpoints are:

- `POST /auth/recovery/password/start`;
- `POST /auth/recovery/mfa/start`.

Both always return the same generic `202` for known, unknown, locked, legally
held and rate-limited subjects. A held subject creates no token, operation or
outbox row. Login likewise maps the internal hold denial to the same public
invalid-credentials response as an unknown Identity and creates no challenge;
authenticated/admin mutation attempts retain typed 403. For an eligible Identity, the same transaction creates
the `RecoveryOperation`, one `RecoveryToken` and an outbox delivery command.
It first locks the Identity and revokes any previous open token with the same
purpose, including an expired-but-unrevoked row; the database partial unique
constraint is the final concurrency guard. It follows that token's immutable
operation link and supersedes only the linked unfinished delivery. Selector
`recovery_id` is exactly `RecoveryToken.id`; delivery and completion never find
an operation by the ambiguous pair `(identity_id, purpose)`. The service
preallocates both UUIDs and encrypts the bound envelope in memory, then inserts
the `PENDING_DELIVERY` operation with its complete envelope shape and linked
token in one transaction; no invalid envelope-less pending row is visible.
The API response, audit records, logs and ordinary outbox event payload never
contain the selector, raw verifier, destination email or decrypted delivery
link. The outbox command contains only `operation_id`; the delivery worker
loads and decrypts the one-time envelope in memory, sends it to the global
Identity email, then CAS-clears the envelope after delivery or expiry.

The on-commit wakeup, outbox consumer and retention retry all compete through
one `PENDING_DELIVERY -> DELIVERING -> DELIVERED` CAS. Only the lease owner may
send. A stale-lease retry reuses the same persisted selector/verifier and
stable persisted provider idempotency key/Message-ID; it never regenerates
delivery material. The AES-GCM associated data binds operation ID, token ID
and purpose so an envelope cannot be moved to another recovery pair. This
gives one normal delivery and byte-identical material if a provider/crash
ambiguity requires retry.

`RecoveryOperation` persists the originating request, correlation and causal
UUIDs. Before each lease owner decrypts the destination, its claim transaction
must commit an append-only `pii.identity_email_read` audit with system actor,
closed delivery reason and operation/attempt IDs only. Audit failure rolls
back the claim, so no PII read/send may occur without durable `who/when/why`
evidence. A stale-lease attempt that decrypts again creates another
attempt-bound read record; raw email and decrypted link never enter audit
metadata.

Recovery delivery is a sensitive Celery task. Provider exceptions are
converted `from None` to a closed non-secret failure and never enter the
generic `BaseEnterpriseTask.on_failure()` path. Recovery DLQ/log output is an
allowlist of typed code, provider class, retryability, string operation ID and
causal identifiers; exception text, traceback, recipient, selector, verifier,
raw args and kwargs are forbidden.

Completion endpoints are:

- `POST /auth/recovery/password/complete`;
- `POST /auth/recovery/mfa/complete`.

They verify the signed selector and constant-time verifier hash, enforce the
token purpose and consume the token through one CAS. In the same transaction,
only its linked operation may CAS `DELIVERED -> COMPLETED` and set
`completed_at`. A tenant administrator
may initiate an MFA recovery for their own membership, but cannot receive or
complete it; delivery goes only to the global Identity owner.

Successful password recovery changes the password, then invokes the sole
global invalidation operation for exactly one version increment and
all-session revoke. Successful MFA recovery revokes active MFA credentials and
backup codes, invokes that same invalidation operation once, moves the Identity to
`MFA_SETUP_REQUIRED` and requires a fresh password login followed by
`MFA_SETUP_PARTIAL`.

### Fail-closed reconciliation

If DB and Redis state diverge during login, refresh, logout, tenant switch or
grant revocation, the request must fail closed. The system must persist a
`SessionReconciliationRecord` or equivalent durable job payload with:

- `session_id`;
- closed `kind` (`REFRESH_ROTATION`, `TENANT_SWITCH` or
  `SESSION_FAMILY_CLEANUP`);
- `operation_id`;
- nullable exact `switch_operation_id`, present only for `TENANT_SWITCH`;
- expected DB state;
- expected Redis state;
- failure reason;
- attempt count;
- `resolved_at`.

An active retention-queue worker reads bounded UUID-ordered candidate IDs,
then for each candidate locks the complete User/merge/session/switch graph in
the global order before claiming the reconciliation row at its declared rank
with a non-blocking advisory work key. Busy candidates are rescheduled; a
target reconciliation row is never `SKIP LOCKED` before its User graph.
`TENANT_SWITCH` guarantees the session remains revoked, closes only its exact
linked pending/db-committed switch operation as failed and deletes the Redis
family. `REFRESH_ROTATION` revokes and deletes without
any switch lookup. `SESSION_FAMILY_CLEANUP` proves the session was already
revoked and only guarantees Redis-family deletion. Each branch CAS-sets
`resolved_at` only after its fail-closed postconditions. It retries transient
failures with backoff and enters the infrastructure DLQ after the configured
retry limit. The worker never recreates a grant or silently restores access
when expected state cannot be proven.

Production rollback never changes security-version validation to `LOG_ONLY`.
The identity issuer and its matching validator are controlled by one atomic
feature mode. Rollback disables both, closes new session/grant endpoints,
revokes ambiguous identity sessions and returns traffic to the complete legacy
auth path. Alternatively, the new path remains enabled and fail-closed.

### JWT compatibility window

Plan 1 adds `identity_id` and `sub` without changing the legacy access-token
claim used by existing domains. In this repository the legacy access
`user_id` is the deterministic UUID derived from the active membership's
immutable `legacy_actor_user` integer PK; it is neither the integer PK itself
nor necessarily the canonical Identity row's actor UUID. During the
compatibility window access JWTs contain:

- `user_id`: unchanged deterministic UUID of the active membership actor;
- `legacy_user_pk`: that actor's Django integer PK;
- `identity_id`: stable global Identity UUID;
- `sub`: the same stable global Identity UUID;
- `iss`: `identity-v1`;
- `aud`: `complect-api`.

New IAM components use `identity_id`/`sub`. Existing gateway, middleware and
`RoleBinding` consumers continue using tenant-local actor `user_id`; bindings
remain on that actor and are not transferred when Identity ownership of a
membership changes. Removing or changing the meaning of `user_id` requires a
separately gated contract change.

Before any permission decision, backend validation must prove that all
identity and tenant claims describe the same records using primary read-only
loads:

- `sub == identity_id`;
- `membership.identity_id == identity_id`;
- `membership.legacy_actor_user_id == legacy_user_pk`;
- the deterministic legacy UUID derived from that membership actor PK equals
  `user_id`;
- grant session, membership and tenant IDs equal the corresponding JWT claims.

Identity claim failures return `IDENTITY_CLAIMS_MISMATCH`; grant/membership
binding failures return `TENANT_CONTEXT_MISMATCH`. The gateway performs the
database-free subset: UUID parsing, `sub == identity_id`, `iss`, `aud`, `exp`,
`type` and `kid`.

The ordinary access validator never takes row locks. Locks are limited to
refresh, tenant switch, revoke, recovery and role/status mutations so parallel
requests in one browser session do not serialize.

The domain-neutral boundary lives in `apps/common/contracts/` as a frozen
`@dataclass(slots=True, frozen=True) AuthenticatedContext` and an
`AuthContextValidationProtocol`. The DTO contains only validated scalar IDs,
tenant/actor/Identity identifiers and context-version snapshots; it contains
no `users` model instance or lazy QuerySet. Common middleware and API helpers
depend only on this Protocol and DTO. A concrete adapter in `apps/users`
performs the primary reads and the checks above, and the application
composition root injects it at startup. `apps/common` must never import
`apps.users`, including through a default implementation or module-level
singleton.

```python
@dataclass(slots=True, frozen=True)
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

Refresh tokens in a JSON body remain a deprecated compatibility input for the
frozen `/api/v1/auth/refresh` contract. Each use increments dedicated
telemetry without logging the token, and the HttpOnly cookie takes precedence.
A zero-usage window is evidence for a future migration but does not authorize
breaking v1. Removing the body input requires an ADR-backed `/api/v2` endpoint
and explicit consumer migration.

At N+3 retirement, legacy refresh storage is removed while the deprecated v1
body parser remains. Temporary `legacy_user_pk` (and legacy display `username`
if present) are removed only in a later cutoff deployment after the complete
pre-cutoff consumer gate below. Backend then derives the actor integer PK from
the active membership's immutable `legacy_actor_user` mapping and still proves
its deterministic UUID equals `user_id`. `user_id` remains the tenant-local
actor contract used by current domain/RBAC consumers and is not a tenant or
credential authority.

Removing `username` or `legacy_user_pk` requires a pre-cutoff client-consumer
gate, not only server telemetry. Before the authority cutoff, the release owner
must complete classified source searches for frontend and generated SDK claim
access, a named inventory and approval from the owner of every external
integration (or an explicit signed statement that none exist), browser
acceptance against candidate tokens with both claims absent. Only after that
evidence is signed may a later cutoff deployment stop issuing the temporary
claims and stop the legacy validator; that deployment then revokes old
sessions, purges legacy refresh state and waits one maximum access-token
lifetime. Local JWT reads are invisible to backend counters, so a zero server
metric cannot replace this evidence.

### Events

New event names must distinguish global identity from tenant membership:

- `identity.created`;
- `identity.password_changed`;
- `identity.password_reset`;
- `identity.mfa_enabled`;
- `identity.mfa_reset`;
- `membership.created`;
- `membership.invited`;
- `membership.enabled`;
- `membership.disabled`;
- `membership.roles_changed`;
- `session.revoked`;
- `auth.login_succeeded`;
- `auth.login_failed`;
- `auth.refresh_reuse_detected`.

Existing `user.*` events remain as compatibility aliases for one migration
window. Each alias carries `compatibility_alias_of` and
`canonical_event_id`; new consumers must subscribe to
identity/membership/session/auth events.

`identity.mfa_reset` means the Identity entered `MFA_SETUP_REQUIRED`; there is
no stable MFA-disabled state. `user.mfa_disabled` is not a default alias: it
may exist for one migration window only when a signed external-consumer
inventory names a real owner and removal release. The current repository has
no registered producer/consumer for that legacy name.

Emit points are exact: `identity.created` is emitted only by live creation of
a new global Identity, never backfill or collision merge;
`identity.password_changed` is emitted only by an owner-authenticated normal
password change; `identity.password_reset` is emitted only by successful
password-recovery consumption.

### Frozen `/api/v1` compatibility

ADR-0003 remains authoritative. Plan 1 does not remove/rename a live v1 DTO
field, change an existing property type or add a required request field. A
request field may move from required to optional because that relaxes what a
client must send. The frozen response contract is stricter: its required-field
set may not shrink, so changing a required response field to optional is a
breaking change. New response fields are allowed only as optional additions.
`LoginRequest.username`, `MFAVerifyRequest.totp_code`, the four existing
`TokenResponse` fields, `MFASetupResponse.secret/otpauth_url` and required
`MFAStatusResponse.mfa_enabled` remain present.

The password step continues the existing convention of carrying a partial
bearer in `TokenResponse.access_token` with `mfa_required=True`; optional
`next_step` identifies MFA setup, MFA verification or tenant selection. A
tenant-selection token in that field is rejected on protected routes. New
tenant/session/backup-code metadata is optional only. The pre-change OpenAPI
is captured as a mandatory baseline and a strengthened compatibility linter
runs before SDK generation. Any future destructive DTO change requires
`/api/v2` and ADR-0003's six-month migration window.

### Migration rollout

Release N's corrective sequence is `0003_identity_expand` followed by
`0003a_identity_audit_fence`. The canonical `0003` blob remains
`d8275cb03cd9055d2b7925993ec1a4395939635d`. At the P0 implementation checkpoint,
disk and the inspected development migration ledger contain only `0001`,
`0002`, `0003`; no separate users schema-drift repair migration exists. Drift
repair remains a separate responsibility stream and a deployment prerequisite
where an environment differs from canonical `0003`; the fence migration does
not restate or repair its schema. The named future `0004` and `0005` retain
their responsibilities and numbers. When implemented, `0004` must depend on
`0003a_identity_audit_fence`, rather than creating a sibling leaf from `0003`.
References below to the `0003 -> 0004` boundary include this Release N fence
expansion. The additional rollback/quiescence gates above apply to all older
rollout and rollback examples in this ADR.

Plan 1 IAM-IDENTITY must use Expand / Backfill / Contract:

- `0003_identity_expand` adds the merge-operation/evidence tables; the same
  Release N artifact includes the minimum Task 2 alias-to-canonical runtime
  guard. Because
  `users.User.identity_id` is still nullable and non-unique, only
  `IdentityMergeOperation.source_user` is an immediate FK to the existing
  integer PK. `source_identity_id` and `canonical_identity_id` are raw,
  immutable UUID columns in `0003`; no unsafe FK may target a non-unique
  expand column. Before any deferred merge, every `RunSQL` has explicit
  `reverse_sql`, every `RunPython` has an explicit backwards callable, and the
  backwards path removes only expand-owned objects while preserving legacy
  authority and pre-existing data.
- `0004_identity_contract_constraints` first establishes and validates
  `UNIQUE(users.User.identity_id)`, then adds the source/canonical UUID FKs as
  `NOT VALID` constraints and validates them. Its reverse drops those FKs in
  dependency-safe order but leaves the raw UUID operation mapping in `0003`.
  A PostgreSQL migration-executor round-trip test must migrate a pre-merge
  fixture from the pre-`0003` state to `0003`, then `0004`, then back through
  `0003` to the starting state and prove schema restoration and legacy-row
  preservation.
- The first committed `APPROVED_DEFERRED` operation establishes an earlier,
  explicit compatibility rollback floor. From that point an application
  startup guard rejects any build older than Task 2's merge-aware canonical
  resolver, and the reverse path out of `0003` raises
  `IrreversibleError` before dropping any object. Schema rollback may go from
  `0004` to `0003`, but never below `0003`; application rollback may keep or
  disable new auth only while retaining the Task 2 guard. The existence of an
  `APPROVED_DEFERRED`, `READY_TO_FINALIZE` or `FINALIZED` row is the durable
  floor evidence. This is a data-dependent compatibility guard, while `0005`
  remains the sole intrinsically irreversible destructive migration.
- `0005_retire_legacy_user_authority` is the sole irreversible migration in
  Plan 1. Its reverse path explicitly raises
  `django.db.migrations.exceptions.IrreversibleError` before reverse DDL; a
  forward no-op/reverse-raise sentinel is last in forward order and therefore
  first in Django's reverse order. This
  ADR approves it as the sole narrowly scoped exception to ADR-0002, permitted
  only after the signed retirement gate proves zero global and tenant IAM hold
  rows and records verified backup/restore and retention-policy evidence.
  Plan 1 has no legal archive; any active hold blocks `0005` rather than being
  exported or bypassed. As its first SQL statement, the atomic migration takes
  the exclusive retirement advisory lock that conflicts with every
  trigger-enforced shared hold latch. It then locks the frozen hold-table
  catalog and repeats the global plus durable-registry/RLS tenant hold scan in
  the same transaction before destructive DDL.

- Release N: add nullable global identity/session/MFA/recovery and merge
  operation/evidence tables, plus compatibility reads and the Task 2
  merge-aware canonical resolver. Pending signed approvals are allowed;
  convergence remains disabled unless the runtime flag and signed coverage
  attestation make the effective gate true.
- Release N: add the duplicate email dry-run report and block contract
  migration while unresolved duplicates exist. A pending approval remains
  unresolved; it must be separately cancelled before a newly signed correction
  may run. Only that correction or `APPROVED_DEFERRED` removes the collision.
- Release N+1: backfill identities and memberships in advisory-coordinated
  batches using the shared bootstrap/Identity lock order; do not log raw
  email/token/MFA data.
- Release N+1: run approved convergence ceremonies under the global sorted
  lock protocol. A `CANCELLED`/`EXPIRED` operation is not itself a contract
  blocker, but its underlying duplicate remains unresolved until a separately
  signed correction or convergence decision clears it.
- Release N+1: membership creation dual-writes the tenant's idempotent
  `TenantAuthorizationState`; a future dedicated tenant provisioning service
  must use the same repository contract.
- Release N+1: dual-read or compatibility adapter proves old and new auth
  claims match for existing single-tenant users.
- Release N+2: switch JWT issuing and refresh rotation to identity/session/grant
  state.
- Release N+2: validate constraints, including merge Identity UUID FKs, enable
  identity/session/grant authority atomically, then stabilize in `ENFORCE`
  while legacy fields remain compatibility shadows.
- Release N+3a pre-cutoff: change user listing/admin/user-info to
  membership-backed paths while temporary JWT claims remain; complete the
  frontend/generated-SDK/external JWT consumer inventory, obtain every named
  owner's approval, pass browser acceptance against candidate tokens without
  `username`/`legacy_user_pk`. This evidence is mandatory before the
  claim/compatibility cutoff; production still emits the temporary claims and
  accepts already-issued legacy tokens during this phase.
- Release N+3a cutoff: only after that signed pre-cutoff gate, stop all
  reads/writes of `User.tenant_id` and legacy MFA/device/refresh authority,
  remove temporary `username`/`legacy_user_pk` claims and `user.*` aliases,
  stop the legacy issuer/validator and deferred-source alias login/recovery
  lookup while retaining raw actor values, finalize status-ready merge
  operations, revoke old sessions, purge legacy refresh storage, wait at least
  one maximum access-token lifetime, preserve the
  deprecated frozen-v1 refresh body input, then soak the zero-use gate for
  seven days.
- Release N+3b: after signed gate evidence, a fresh check proving no global or
  tenant IAM hold, and verified backup/restore plus retention evidence, apply
  irreversible migration `0005` under the exclusive retirement barrier to
  remove direct `tenant_id`, tenant indexes, plaintext `mfa_secret`,
  `mfa_enabled` and global `device_fingerprint` columns. `password`, integer
  PK, `identity_id` and the stable tenant-local actor `user_id` contract
  remain.

Before the first deferred merge, full legacy rollback must be possible through
N+2 stabilization. After that merge, rollback is bounded by Task 2 plus
`0003`; after N+3b only an identity-only rollback compatible with the
contracted schema is valid. During mixed mode, authorization must prefer the
fail-closed path when old and new state disagree.

The database stabilization command proves only durable database invariants.
Gateway runtime checks, limiter Prometheus queries, OpenAPI/SDK verification,
browser acceptance and migration-lint disposition are separate release-runbook
gates. Shadow-read coverage is measured from paired durable eligible/compared
audit records over the soak window, with zero missing comparisons and zero
mismatches; no fixed high-volume threshold is used for a low-traffic tenant.

### Правила

- Global security models do not inherit `BaseEnterpriseModel`.
- Existing `users.User` is the physical Identity table; its integer PK remains
  for Django compatibility, and `identity_id UUID UNIQUE` is the stable Identity
  identifier.
- Every membership keeps one immutable `legacy_actor_user` source mapping;
  canonical Identity ownership may change only through approved reconciliation,
  while tenant-local historical actor and `RoleBinding` authority do not move.
- Signed merge approval only creates
  `IdentityMergeOperation(APPROVED_PENDING_CONVERGENCE)`; it never performs an
  authority mutation. `CANCELLED` and `EXPIRED` close only that approval and
  do not prevent a separately signed resolution of the still-unresolved
  duplicate.
- Participation as source or canonical Identity in a live
  `APPROVED_PENDING_CONVERGENCE` operation makes `--correct-email` fail with
  `MERGE_OPERATION_ACTIVE`. The pending operation must first be
  cancelled by its own signed `--cancel-merge` action; correction then requires
  a new purpose-bound signature over the post-lock participant-operation
  digest and never acts as an implicit cancellation. A signature bound to the
  pre-cancellation graph fails with `APPROVAL_CONTEXT_STALE`.
- `APPROVED_PENDING_CONVERGENCE -> APPROVED_DEFERRED` is the only merge point.
  It requires either owner-confirmed password/canonical-MFA convergence or the
  complete dormant fail-closed predicate, plus drained security artifacts and
  a full no-hold recheck.
- From deferred convergence until the Task 16 cutoff, source usernames are
  compatibility aliases; afterward they are historical/display values only.
  In both phases the canonical Identity is the sole password, MFA, recovery,
  security status/version, device, session and token authority.
- Finalization readiness follows current canonical status. `ACTIVE` requires
  owner-convergence evidence, a usable canonical password and confirmed
  canonical MFA. `LOCKED`, `PENDING_CREDENTIALS` and `MFA_SETUP_REQUIRED`
  instead require the complete fail-closed session/grant/refresh/challenge/
  recovery/reconciliation drain and no hold; password/MFA shadow equality is
  irrelevant even if the earlier convergence mode was owner-confirmed.
- A `DORMANT_FAIL_CLOSED` deferred/ready graph cannot become active. It must
  finalize while inactive at Task 16; only then may normal canonical
  onboarding or membership enablement proceed.
- Every merge-graph mutation uses the shared sorted lock coordinator and
  retries full discovery when the graph changes; there are no incremental
  participant locks.
- Deferred merge is executable only when both the configured flag and a valid,
  current, exact-build/runtime coverage attestation make its effective flag
  true.
- Merge hold observation, signed requeue and requeue result are separate
  append-only evidence. A held operation/source/canonical is unchanged and no
  hold release resumes work automatically.
- After the first deferred merge, Task 2's canonical resolver and migration
  `0003` are the minimum rollback floor.
- Tenant-scoped IAM models inherit `BaseEnterpriseModel`. Plan 1's grant uses
  append-only lifecycle rules; Plan 2's future `InviteToken` must follow the
  lifecycle specified here, where `is_deleted` is not an authz input.
- A browser identity session has at most one active tenant grant.
- Tenant admins may revoke only grants and invitations inside their tenant.
- Global logout-all revokes all identity sessions.
- Tenant membership disable revokes only grants for that membership and
  increments `membership.authz_version`.
- Any operation that creates a grant first supersedes an existing open/current
  grant for the same session, even when that grant is expired.
- Cross-tenant grant switching goes only through `TenantGrantSwitchRepository`.
- `session_family_id` is unique, and an absent Redis family is initialized only
  from a matching initial `DB_COMMITTED` operation at generation zero.
- Tenant-switch idempotency covers the exact token/cookie response through a
  short-lived encrypted envelope; grant-row idempotency alone is insufficient.
- Expired tenant-selection credentials can only replay already-resulted work;
  they cannot bypass expiry for a new or pending operation.
- Post-backfill global IAM gates discover tenant IDs through the durable
  registry implementation of `IamTenantInventoryProvider` and query FORCE-RLS
  tables one explicit tenant context at a time.
- Every durable session reconciliation record is processed fail-closed until
  resolved or placed in DLQ; reconciliation never restores access.
- Reconciliation kind controls the worker branch; refresh and cleanup records
  never search for a tenant-switch operation.
- Recovery token/operation form one immutable pair; selector lookup starts at
  token ID, and completion closes only the linked operation.
- Global auth audit payload is database append-only; only a monotonic legal
  hold latch may change.
- Access tokens are stored only in browser memory. The only server-side bearer
  replay exception is the short-lived encrypted tenant-switch response
  envelope; plaintext tokens never enter ordinary tables, logs, cache or
  outbox. Refresh tokens remain HttpOnly Secure cookies.
- JWT is invalid if any embedded version differs from DB/cache authority.
- Typed auth failures use the exhaustive central HTTP policy; infrastructure
  failures never masquerade as invalid credentials.
- Exactly one global invalidation operation owns each security-version
  increment plus all-session revoke; a bulk revoke helper never increments it.
- Legal hold blocks every IAM mutation and retention action; no security,
  reconciliation or cryptoshredding path bypasses the project invariant.
- Any global or tenant IAM legal hold blocks irreversible N+3b retirement;
  Plan 1 has no legal-archive escape hatch.
- PII-capable IAM queries execute only after their append-only audit write has
  committed and never place raw email or returned PII in that audit.
- Duplicate normalized emails require manual reconciliation before
  `UNIQUE(normalized_email)` enforcement.
- Plan 1 is not complete until Task 16 removes legacy refresh/issuer authority
  and Task 17/N+3b removes the retired tenant/MFA/device schema after the
  signed zero-use gate.

### Запреты

- Do not use `users.User.tenant_id` as security or authorization authority.
- Do not attach `tenant_id` to `IdentitySession`, `MfaCredential`,
  `BackupCode` or `RecoveryToken`.
- Do not add `IdentitySession.current_grant_id`.
- Do not auto-merge identities by normalized email.
- Do not mutate membership, email, credentials or security authority when a
  merge is merely approved, cancelled or expired.
- Do not compare a deferred source password/MFA shadow or accept its
  status/version/session/token state as authority; resolve the canonical
  Identity first.
- Do not transition a merge to deferred on a configuration flag alone, with a
  stale/wrong-build coverage attestation, or through an unlisted runtime entry
  point.
- Do not acquire a newly discovered merge-graph lock incrementally; release
  and retry the globally sorted protocol from discovery.
- Do not encode legal hold as an `IdentityMergeOperation` state, mutate a held
  merge graph, or auto-resume merely because a later scan sees no hold.
- Do not reverse below `0003` or start a pre-Task-2 build after durable
  deferred-merge floor evidence exists.
- Do not accept `@identity.invalid` for ordinary accounts, invitations,
  corrected email or recovery delivery.
- Do not merge overlapping tenant footprints, accept an ambiguous membership
  actor, rewrite `legacy_actor_user`, or transfer current `RoleBinding` during
  Identity reconciliation.
- Do not move password hash out of `users.User` in Plan 1 IAM-IDENTITY.
- Do not replace `AUTH_USER_MODEL` with a new Identity table in Plan 1.
- Do not let tenant admins inspect other memberships of the same identity.
- Do not base director/admin restrictions on role name strings.
- Do not let ordinary soft delete alter grant authorization state.
- Do not update immutable grant authority fields after insert or return a
  stored switch response after its session/context becomes invalid.
- Do not run tenant selection through a catch-all access-JWT Gateway plugin or
  accept its partial bearer on another route.
- Do not infer reconciliation behavior from the presence of a switch row;
  branch on the persisted closed kind.
- Do not resolve recovery delivery/completion by Identity plus purpose or send
  provider exception text/traceback to the generic DLQ.
- Do not mutate or delete auth audit evidence after insert.
- Do not import `apps.users` from `apps.common`; wire the users-owned auth
  validator to the common Protocol at the composition root.
- Do not let ordinary soft delete alter invite validity.
- Do not put a full bearer invite token into the signed selector.
- Do not include `email_normalization_version` in the normalized email unique
  constraint.
- Do not expose generic all-tenant ORM access to implement tenant switching.
- Do not implement global IAM gates with an RLS-bypassing membership/grant
  query or an ad hoc tenant inventory.
- Do not log passwords, tokens, MFA secrets, backup codes or raw reconciliation
  reports.

---

## 4. Рассмотренные альтернативы

| Альтернатива | Причина отказа |
| ------------ | -------------- |
| One email belongs to exactly one tenant forever | Blocks multi-membership and requires duplicate accounts for the same person. |
| New `Identity(UUID PK)` table with `users.User` as OneToOne adapter | Cleaner greenfield model, but riskier for this codebase because existing Django auth and domain references already depend on `users.User` integer PK. |
| Keep `tenant_id` on Django User as the authority | Couples credentials to tenant state and makes cross-tenant membership unsafe. |
| Put dummy `tenant_id` on global security tables | Creates false RLS semantics and can bind MFA/session state to the wrong tenant. |
| `IdentitySession.current_grant_id` FK | Crosses global-to-tenant boundary and makes RLS-protected grant lookup awkward. |
| Soft-delete grants like ordinary tenant records | Generic delete paths could accidentally change security decisions. |
| Soft-delete invite tokens like ordinary tenant records | Standard managers could hide a token row and make validity depend on generic repository behavior instead of explicit consumed/revoked/expired state. |
| Auto-merge duplicate emails during backfill | Same normalized email is not proof of same person and may create account takeover risk. |
| Mutate credentials, memberships and email in the signed approval command | Approval proves an operator decision, not owner control of both authenticating identities; immediate merge can preserve two competing credential authorities or lock out the owner. |
| Copy whichever existing source/canonical credential wins | Makes collision resolution choose an authority implicitly. Active convergence instead requires one newly encoded password plus canonical MFA proof; dormant convergence is allowed only for a fully disabled and drained graph. |
| Store `BLOCKED_BY_LEGAL_HOLD` on the merge operation | Mutating the held aggregate violates legal-hold semantics and makes hold release look like an automatic state-machine input. Separate append-only hold/requeue/result evidence preserves both rules. |
| Enable deferred merge with a feature flag alone | A flag cannot prove that every deployed login/recovery/session entry point resolves aliases canonically. Exact-build signed coverage attestation is also required. |
| Lock rows as each merge edge is discovered | Concurrent graph expansion can invert lock order and deadlock or split the graph. Complete discovery, globally sorted locks and typed full retry provide one total order. |
| `UNIQUE(normalized_email, email_normalization_version)` | Allows the same normalized email to exist twice across normalization versions and breaks the global one-email-one-identity invariant. |
| Disable RLS or use generic all-tenant ORM access for grant switching | Solves the transaction mechanically but creates a broad data-leak primitive outside the narrow session switch boundary. |
| One `authz_version` for all revocation causes | Role changes in one tenant would unnecessarily invalidate unrelated tenant work. |

---

## 5. Последствия

### Плюсы

- Supports one real person with memberships in multiple tenant.
- Avoids high-risk rewrites of existing `users.User` integer-PK references.
- Separates global compromise response from tenant-local administration.
- Allows tenant admins to manage local employee access without seeing other
  tenants.
- Gives deterministic invalidation for password, MFA, role, policy and tenant
  switch changes.
- Handles expired open grants without blocking future switch, login or refresh
  grant creation.
- Makes token/session reconciliation auditable and recoverable.
- Separates manual collision approval from owner-proved or dormant fail-closed
  credential convergence, preventing two live credential authorities.
- Makes merge concurrency, legal-hold interruption and runtime-path coverage
  explicit, typed and auditable.
- Keeps future custom roles and director restrictions capability-based.

### Минусы

- Plan 1 requires a careful compatibility period and manual email
  reconciliation gate.
- Auth services become stateful around identity sessions and tenant grants,
  not just Redis refresh keys.
- JWT validation must check more version claims.
- Tenant switch becomes a transactional security operation with idempotency,
  not a simple frontend context change.
- `TenantGrantSwitchRepository` needs explicit tests for two tenant contexts in
  one transaction.
- Active collision convergence requires an owner ceremony, canonical MFA proof
  and complete session/token drain; dormant convergence deliberately has a
  narrow all-disabled predicate.
- Merge execution depends on signed, expiring, exact-build entry-point
  attestations and a shared cross-table lock coordinator.
- Once the first merge reaches `APPROVED_DEFERRED`, rollback below Task 2 or
  migration `0003` is intentionally unavailable.

---

## 6. Связанные ADR и разделы

- [ADR-0002](0002-zero-downtime-migration-governance.md) — Expand / Backfill / Contract rollout
- [ADR-0003](0003-openapi-schema-freeze.md) — API compatibility and SDK generation gates
- [ADR-0005](0005-bounded-context-ownership.md) — domain boundaries and protocol-based access
- [ADR-0006](0006-jwt-auth-token-rotation.md) — existing JWT and refresh rotation model, partially superseded
- [ADR-0007](0007-api-gateway-trust-boundary.md) — gateway headers and protected API boundary
- [ADR-0009](0009-pii-vault-query-masking.md) — PII handling for email and recovery artifacts
