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
- `TenantMembership` tenant-scoped и содержит ссылку на `identity_id`;
- один `Identity` может иметь несколько active или inactive memberships;
- в одной браузерной `IdentitySession` активен не более один tenant grant;
- все protected API продолжают получать `tenant_id` только из проверенного JWT,
  не из body/query params.

### Модели

Глобальные модели без `BaseEnterpriseModel` и без RLS:

- `users.User` as Identity: integer Django PK, `identity_id UUID UNIQUE`,
  normalized email, original email, password hash, status, `security_version`,
  `email_normalization_version`, timestamps, legal retention fields.
- `IdentitySession`: `identity_id`, `session_family_id`, device metadata,
  refresh token family metadata, `revoked_at`, `expires_at`; references
  `users.User.identity_id`, not the Django integer PK.
- `MfaCredential`: `identity_id`, credential type, encrypted secret,
  `key_id`, confirmation state, timestamps; references
  `users.User.identity_id`.
- `BackupCode`: `identity_id`, code hash, `key_id`, used state; references
  `users.User.identity_id`.
- `RecoveryToken`: `identity_id`, token hash, purpose, TTL, consumed/revoked
  state; references `users.User.identity_id`.
- `SessionReconciliationRecord`: durable recovery payload for failed
  session/grant/Redis transitions.

Tenant-scoped модели с `BaseEnterpriseModel`, tenant filtering, RLS и soft
delete, кроме security lifecycle исключений ниже:

- `TenantMembership`: `tenant_id`, `identity_id`, employee profile fields,
  status, `authz_version`.
- `InviteToken`: `tenant_id`, `membership_id`, token hash, TTL,
  consumed/revoked state, `version`.
- `TenantSessionGrant`: `tenant_id`, `session_id`, `membership_id`,
  `context_version`, issued/expires/superseded/revoked timestamps.
- `TenantRole`: tenant-local role definition.
- `MembershipRoleBinding`: role assignment for a membership.
- `TenantAuthorizationState`: tenant-wide `policy_version`.
- `ObjectAssignment`: assigned resource access, covered by a later ADR.

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

Tenant-scoped models continue to use the existing tenant-aware repository
contract. `TenantSessionGrant` is an exception to ordinary soft delete:
authorization decisions must use only explicit security states.

### Email normalization and reconciliation

Email uniqueness is enforced only after a manual reconciliation gate.

Rollout order:

1. Introduce `email_normalization_version = 1`.
2. Backfill normalized email into candidate identities.
3. Run a dry-run management command that reports duplicate normalized emails
   across existing tenants.
4. Store the reconciliation report as a protected PII artifact outside git and
   outside application logs.
5. Manually classify each duplicate as one `Identity` with multiple
   memberships or as separate people requiring corrected email data.
6. Add the final unique constraint on `normalized_email` only after unresolved
   duplicates are zero.

Automatic merge by email is forbidden.

`email_normalization_version` is technical provenance for the value currently
stored in `normalized_email`. It is not part of the uniqueness key.

When normalization rules change:

1. Calculate `normalized_email_candidate` with the new algorithm.
2. Run reconciliation against candidate collisions.
3. Resolve collisions manually.
4. Replace `normalized_email` with the candidate value.
5. Keep `UNIQUE(normalized_email)` as the global invariant.

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

### Tenant switching

`POST /auth/tenant/select/` switches the active tenant inside the current
identity session.

Rules:

- request requires an `Idempotency-Key` or internal `switch_operation_id`;
- replay of the same operation returns the already-created grant result;
- replay must not create a second grant;
- replay must not increment `context_version` twice;
- replay must not revoke a successfully switched session;
- switch operation supersedes any current open grant for the session,
  regardless of `expires_at`, and creates a new grant in one DB transaction;
- refresh token rotates in the same logical operation;
- old browser tabs receive `TENANT_SWITCHED` after backend version mismatch;
- frontend may use `BroadcastChannel`, but backend version checks are the
  source of truth.

`IdentitySession` remains valid when tenant is switched. Only the previous
tenant grant is superseded.

The same open-grant supersede rule applies to:

- manual `POST /auth/tenant/select/`;
- auto-select tenant after login;
- context restoration or tenant re-selection during refresh.

### RLS-safe grant switching repository

Tenant switching crosses two tenant contexts in one security operation. Generic
tenant-scoped repositories are not allowed to perform this by disabling RLS or
by exposing all-tenant ORM access.

Introduce a narrow `TenantGrantSwitchRepository` infrastructure contract:

1. Start a DB transaction.
2. Lock the global `IdentitySession`.
3. In the old tenant DB context, lock and CAS-supersede the existing
   open/current grant, including expired grants.
4. Switch the local DB tenant context to the target tenant.
5. Verify the target `TenantMembership` belongs to the session identity and is
   enabled.
6. Create the new `TenantSessionGrant`.
7. Rotate refresh token state as part of the same logical operation.
8. Commit transaction; on failure, persist `SessionReconciliationRecord` and
   fail closed.

This repository is not a general cross-tenant data access API. It may access
only `IdentitySession`, `TenantSessionGrant`, `TenantMembership` and required
session reconciliation records for the switch operation.

Plan 1 IAM-IDENTITY must include tests for:

- tenant A to tenant B;
- tenant B to tenant A;
- tenant A to tenant A idempotent replay;
- old open grant already expired;
- target membership disabled;
- failure after supersede and before insert creates reconciliation state and
  does not silently restore access.

### Refresh rotation and reuse detection

Refresh token rotation remains mandatory, but it becomes session-family aware:

- every refresh token belongs to an `IdentitySession.session_family_id`;
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

### Cryptographic key rotation

Encrypted MFA secrets, backup code selectors and signed token selectors must
store `key_id` or equivalent key version. Runtime verification must support
current and previous allowed keys during rotation.

Key rotation must not force all users to reset MFA.

### Fail-closed reconciliation

If DB and Redis state diverge during login, refresh, logout, tenant switch or
grant revocation, the request must fail closed. The system must persist a
`SessionReconciliationRecord` or equivalent durable job payload with:

- `session_id`;
- `operation_id`;
- expected DB state;
- expected Redis state;
- failure reason;
- attempt count;
- `resolved_at`.

The reconciliation worker may revoke ambiguous grants or sessions. It must not
silently restore access when the expected state cannot be proven.

### Events

New event names must distinguish global identity from tenant membership:

- `identity.created`;
- `identity.password_changed`;
- `identity.password_reset`;
- `identity.mfa_enabled`;
- `identity.mfa_disabled`;
- `membership.created`;
- `membership.invited`;
- `membership.enabled`;
- `membership.disabled`;
- `membership.roles_changed`;
- `session.revoked`;
- `auth.login_succeeded`;
- `auth.login_failed`;
- `auth.refresh_reuse_detected`.

Existing `user.*` events may remain as compatibility aliases during migration,
but new consumers must subscribe to identity/membership/session/auth events.

### Migration rollout

Plan 1 IAM-IDENTITY must use Expand / Backfill / Contract:

- Release N: add nullable global identity/session/MFA/recovery tables and
  compatibility reads.
- Release N: add duplicate email dry-run report and block contract migration
  while unresolved duplicates exist.
- Release N+1: backfill identities and memberships in batches with
  `SKIP LOCKED`; do not log raw email/token/MFA data.
- Release N+1: dual-read or compatibility adapter proves old and new auth
  claims match for existing single-tenant users.
- Release N+2: switch JWT issuing and refresh rotation to identity/session/grant
  state.
- Release N+2: validate constraints and remove old direct `tenant_id`,
  `mfa_secret`, `mfa_enabled` and `device_fingerprint` authority from
  `users.User`; `password`, integer PK and `identity_id` remain on
  `users.User`.

Rollback must be possible at each release boundary. During mixed mode,
authorization must prefer the fail-closed path when old and new state disagree.

### Правила

- Global security models do not inherit `BaseEnterpriseModel`.
- Existing `users.User` is the physical Identity table; its integer PK remains
  for Django compatibility, and `identity_id UUID UNIQUE` is the stable Identity
  identifier.
- Tenant-scoped IAM models inherit `BaseEnterpriseModel`, except append-only
  grant lifecycle rules and InviteToken lifecycle rules where `is_deleted` is
  not an authz input.
- A browser identity session has at most one active tenant grant.
- Tenant admins may revoke only grants and invitations inside their tenant.
- Global logout-all revokes all identity sessions.
- Tenant membership disable revokes only grants for that membership and
  increments `membership.authz_version`.
- Any operation that creates a grant first supersedes an existing open/current
  grant for the same session, even when that grant is expired.
- Cross-tenant grant switching goes only through `TenantGrantSwitchRepository`.
- Access tokens are stored only in memory; refresh tokens remain HttpOnly
  Secure cookies.
- JWT is invalid if any embedded version differs from DB/cache authority.
- Duplicate normalized emails require manual reconciliation before
  `UNIQUE(normalized_email)` enforcement.

### Запреты

- Do not use `users.User.tenant_id` as security or authorization authority.
- Do not attach `tenant_id` to `IdentitySession`, `MfaCredential`,
  `BackupCode` or `RecoveryToken`.
- Do not add `IdentitySession.current_grant_id`.
- Do not auto-merge identities by normalized email.
- Do not move password hash out of `users.User` in Plan 1 IAM-IDENTITY.
- Do not replace `AUTH_USER_MODEL` with a new Identity table in Plan 1.
- Do not let tenant admins inspect other memberships of the same identity.
- Do not base director/admin restrictions on role name strings.
- Do not let ordinary soft delete alter grant authorization state.
- Do not let ordinary soft delete alter invite validity.
- Do not put a full bearer invite token into the signed selector.
- Do not include `email_normalization_version` in the normalized email unique
  constraint.
- Do not expose generic all-tenant ORM access to implement tenant switching.
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

---

## 6. Связанные ADR и разделы

- [ADR-0002](0002-zero-downtime-migration-governance.md) — Expand / Backfill / Contract rollout
- [ADR-0003](0003-openapi-schema-freeze.md) — API compatibility and SDK generation gates
- [ADR-0005](0005-bounded-context-ownership.md) — domain boundaries and protocol-based access
- [ADR-0006](0006-jwt-auth-token-rotation.md) — existing JWT and refresh rotation model, partially superseded
- [ADR-0007](0007-api-gateway-trust-boundary.md) — gateway headers and protected API boundary
- [ADR-0009](0009-pii-vault-query-masking.md) — PII handling for email and recovery artifacts
