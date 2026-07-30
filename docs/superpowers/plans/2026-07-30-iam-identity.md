# IAM-IDENTITY Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate authentication from single-tenant `users.User` state to the ADR-0024 Identity, Membership, IdentitySession and TenantSessionGrant foundation without replacing `AUTH_USER_MODEL`.

**Architecture:** Existing `users.User` becomes the physical Identity table: integer PK remains for Django compatibility, and `identity_id UUID UNIQUE` becomes the stable identity identifier. Tenant state moves into `TenantMembership`; browser session state moves into global `IdentitySession` plus tenant-scoped `TenantSessionGrant`. Rollout is expand/backfill/contract with fail-closed validation and no automatic email merges.

**Tech Stack:** Django 5.2, django-ninja, PostgreSQL 16, Redis DB 4 for idempotency/session cache, PyJWT HS256, pyotp, Docker-only pytest.

## Global Constraints

- Follow ADR-0024: `AUTH_USER_MODEL` remains `users.User`; do not create a new `Identity(UUID PK)` table.
- Do not rewrite existing domain FK references to `users.User` in Plan 1.
- Global security models do not inherit `BaseEnterpriseModel` and do not contain `tenant_id`.
- Tenant-scoped IAM models use tenant filtering and RLS; `TenantSessionGrant` and `InviteToken` must not use generic soft delete for security decisions.
- Final email uniqueness is `UNIQUE(normalized_email)`, not `(normalized_email, email_normalization_version)`.
- Duplicate normalized emails must block contract migration until manually reconciled.
- Every grant creation must supersede any open/current grant for the same `IdentitySession`, including expired grants.
- Cross-tenant grant switching must go through a narrow `TenantGrantSwitchRepository`; no generic all-tenant ORM access.
- JWT validation must check `identity_security_version`, `membership_authz_version`, `tenant_policy_version`, and `grant_context_version`.
- Access tokens stay in memory; refresh tokens stay in HttpOnly Secure cookies.
- Mutations use repository/CAS style; avoid direct `.save()` and `.delete()` in command-side code except Django fixture/test setup.
- Tests run through Docker: use `make test-common`, `make test-fast`, targeted `docker compose exec backend pytest ...`, and never SQLite.

---

## File Structure

- Modify `backend/apps/users/models.py`: add identity fields to `User`; add `IdentitySession`, `MfaCredential`, `BackupCode`, `RecoveryToken`, `SessionReconciliationRecord`, `TenantMembership`, `InviteToken`, `TenantSessionGrant`, `TenantAuthorizationState`.
- Create `backend/apps/users/services/email_normalization.py`: normalize email with versioned algorithm and candidate calculation.
- Create `backend/apps/users/management/commands/report_identity_email_duplicates.py`: dry-run duplicate report gate.
- Create `backend/apps/users/repositories/identity_repository.py`: tenant-free CAS repository for global security records.
- Create `backend/apps/users/repositories/tenant_grant_switch_repository.py`: narrow RLS-safe session grant switch repository.
- Create `backend/apps/users/services/invite_token_service.py`: signed selector plus verifier hash issue/consume/revoke.
- Create `backend/apps/users/services/identity_session_service.py`: login, refresh rotation, reuse detection, logout-all, session family state.
- Modify `backend/apps/users/services/auth_service.py`: compatibility wrapper over identity session service.
- Modify `backend/apps/users/services/mfa_service.py`: move MFA from `User.mfa_secret`/`mfa_enabled` into `MfaCredential` and backup codes.
- Modify `backend/apps/users/api.py`: add `/auth/tenant/select/`, `/auth/sessions/`, logout-all, recovery endpoints, and versioned token responses.
- Modify `backend/apps/common/api/auth.py`: validate the four JWT version claims against DB/cache authority.
- Modify `backend/apps/common/middleware/idempotency.py`: allow `/api/v1/auth/tenant/select/` to enforce internal idempotency inside auth service despite the auth-prefix middleware exemption.
- Create tests under `backend/apps/users/tests/`: identity models, email normalization, duplicate report, invite tokens, tenant grant switch, identity sessions, compatibility auth.
- Modify `backend/apps/users/schemas/auth.py`: add request/response schemas for email login, tenant select, session list, recovery, MFA setup/confirm/backup codes.

---

### Task 1: Identity and Membership Schema

**Files:**
- Modify: `backend/apps/users/models.py`
- Create: `backend/apps/users/migrations/0003_identity_membership_session_schema.py`
- Test: `backend/apps/users/tests/test_identity_models.py`

**Interfaces:**
- Produces: `User.identity_id: uuid.UUID`, `User.normalized_email: str`, `User.security_version: int`, `TenantMembership.authz_version: int`, `TenantSessionGrant.context_version: int`.
- Produces: model method-free schema only; command-side transitions are added in later tasks.
- Consumes: `BaseEnterpriseModel` for tenant-scoped records except explicit lifecycle restrictions for grant/invite.

- [ ] **Step 1: Write failing model tests**

Add `backend/apps/users/tests/test_identity_models.py`:

```python
import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.users.models import IdentitySession, TenantMembership, TenantSessionGrant, User


@pytest.mark.django_db(transaction=True)
def test_user_has_stable_identity_id_and_versions():
    user = User.objects.create_user(username="ivan@example.com", email="ivan@example.com", password="Pass1!")

    assert isinstance(user.identity_id, uuid.UUID)
    assert user.security_version == 1
    assert user.email_normalization_version == 1
    assert user.normalized_email == "ivan@example.com"
    assert user.tenant_id is None or user.tenant_id == user.tenant_id


@pytest.mark.django_db(transaction=True)
def test_normalized_email_is_globally_unique():
    User.objects.create_user(username="one@example.com", email="Ivan@Example.com", password="Pass1!")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            User.objects.create_user(username="two@example.com", email="ivan@example.com", password="Pass1!")


@pytest.mark.django_db(transaction=True)
def test_membership_is_tenant_scoped_identity_link():
    identity = User.objects.create_user(username="member@example.com", email="member@example.com", password="Pass1!")
    tenant_id = uuid.uuid4()

    membership = TenantMembership.objects.create(
        tenant_id=tenant_id,
        identity_id=identity.identity_id,
        email=identity.email,
        full_name="Ivan Petrov",
        status=TenantMembership.Status.ACTIVE,
    )

    assert membership.tenant_id == tenant_id
    assert membership.identity_id == identity.identity_id
    assert membership.authz_version == 1


@pytest.mark.django_db(transaction=True)
def test_only_one_open_grant_per_session_even_if_old_grant_expired():
    identity = User.objects.create_user(username="session@example.com", email="session@example.com", password="Pass1!")
    tenant_id = uuid.uuid4()
    membership = TenantMembership.objects.create(
        tenant_id=tenant_id,
        identity_id=identity.identity_id,
        email=identity.email,
        status=TenantMembership.Status.ACTIVE,
    )
    session = IdentitySession.objects.create(identity_id=identity.identity_id, expires_at=timezone.now() + timedelta(days=7))
    TenantSessionGrant.objects.create(
        tenant_id=tenant_id,
        session_id=session.id,
        membership_id=membership.id,
        context_version=1,
        issued_at=timezone.now() - timedelta(days=2),
        expires_at=timezone.now() - timedelta(days=1),
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TenantSessionGrant.objects.create(
                tenant_id=tenant_id,
                session_id=session.id,
                membership_id=membership.id,
                context_version=2,
                issued_at=timezone.now(),
                expires_at=timezone.now() + timedelta(hours=8),
            )
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest backend/apps/users/tests/test_identity_models.py -v
```

Expected: FAIL because identity fields and new models do not exist.

- [ ] **Step 3: Implement schema**

In `backend/apps/users/models.py`, add fields to `User`:

```python
identity_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
normalized_email = models.EmailField(max_length=254, unique=True, null=True, blank=True)
email_normalization_version = models.PositiveSmallIntegerField(default=1, editable=False)
security_version = models.PositiveIntegerField(default=1, editable=False)
security_status = models.CharField(max_length=32, default="active", db_index=True)
identity_legal_hold = models.BooleanField(default=False, db_index=True)
```

Add global models with UUID primary keys and no `tenant_id`: `IdentitySession`, `MfaCredential`, `BackupCode`, `RecoveryToken`, `SessionReconciliationRecord`.

Add tenant-scoped models: `TenantMembership`, `InviteToken`, `TenantSessionGrant`, `TenantAuthorizationState`. Use `BaseEnterpriseModel` for tenant-scoped columns, but add comments and managers making grant/invite security lookup explicit.

For `TenantSessionGrant`, add the partial unique constraint:

```python
models.UniqueConstraint(
    fields=["session_id"],
    condition=models.Q(superseded_at__isnull=True, revoked_at__isnull=True),
    name="uniq_users_open_grant_per_session",
)
```

For `User`, add `UniqueConstraint(fields=["normalized_email"], name="uniq_users_normalized_email")`. Do not include `email_normalization_version`.

- [ ] **Step 4: Create migration**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py makemigrations users --name identity_membership_session_schema
```

Then inspect the generated migration and ensure:

```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py sqlmigrate users 0003
```

Expected: migration adds nullable/expand-safe columns first; no non-null field without safe default; no `tenant_id` on global models.

- [ ] **Step 5: Run tests and migration checks**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest backend/apps/users/tests/test_identity_models.py -v
docker compose -f docker-compose.dev.yml exec backend python infra/scripts/migration_linter.py backend/apps/users/migrations/0003_identity_membership_session_schema.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/users/models.py backend/apps/users/migrations/0003_identity_membership_session_schema.py backend/apps/users/tests/test_identity_models.py
git commit -m "feat(iam): add identity membership session schema"
```

---

### Task 2: Email Normalization and Reconciliation Gate

**Files:**
- Create: `backend/apps/users/services/email_normalization.py`
- Create: `backend/apps/users/management/commands/report_identity_email_duplicates.py`
- Test: `backend/apps/users/tests/test_email_reconciliation.py`

**Interfaces:**
- Produces: `normalize_email(email: str) -> NormalizedEmail`
- Produces: `find_duplicate_normalized_emails() -> list[EmailDuplicateGroup]`
- Consumes: `User.email`, `User.normalized_email`, `User.email_normalization_version`

- [ ] **Step 1: Write failing tests**

Add `backend/apps/users/tests/test_email_reconciliation.py`:

```python
import json
import uuid

import pytest
from django.core.management import call_command

from apps.users.services.email_normalization import normalize_email
from apps.users.models import TenantMembership, User


def test_normalize_email_version_1_lowercases_domain_and_local():
    normalized = normalize_email(" Ivan.Example@EXAMPLE.COM ")

    assert normalized.value == "ivan.example@example.com"
    assert normalized.version == 1


@pytest.mark.django_db(transaction=True)
def test_duplicate_report_groups_existing_cross_tenant_emails(tmp_path):
    identity_one = User.objects.create_user(username="one", email="ivan@example.com", password="Pass1!")
    identity_two = User.objects.create_user(username="two", email="ivan+legacy@example.com", password="Pass1!")
    User.objects.filter(pk=identity_two.pk).update(normalized_email="ivan@example.com")

    TenantMembership.objects.create(
        tenant_id=uuid.uuid4(),
        identity_id=identity_one.identity_id,
        email="ivan@example.com",
        status=TenantMembership.Status.ACTIVE,
    )
    TenantMembership.objects.create(
        tenant_id=uuid.uuid4(),
        identity_id=identity_two.identity_id,
        email="IVAN@EXAMPLE.COM",
        status=TenantMembership.Status.ACTIVE,
    )

    output = tmp_path / "identity-email-duplicates.json"
    call_command("report_identity_email_duplicates", "--output", str(output), "--format", "json")

    payload = json.loads(output.read_text())
    assert payload["unresolved_duplicate_groups"] == 1
    assert payload["groups"][0]["normalized_email"] == "ivan@example.com"
    assert len(payload["groups"][0]["members"]) == 2
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest backend/apps/users/tests/test_email_reconciliation.py -v
```

Expected: FAIL because service and command do not exist.

- [ ] **Step 3: Implement normalization service**

Create `backend/apps/users/services/email_normalization.py`:

```python
from dataclasses import dataclass

EMAIL_NORMALIZATION_VERSION = 1


@dataclass(frozen=True, slots=True)
class NormalizedEmail:
    value: str
    version: int


def normalize_email(email: str) -> NormalizedEmail:
    value = email.strip().casefold()
    return NormalizedEmail(value=value, version=EMAIL_NORMALIZATION_VERSION)
```

- [ ] **Step 4: Implement duplicate report command**

Create `backend/apps/users/management/commands/report_identity_email_duplicates.py`. The command must:

- accept `--output <path>` and `--format json`;
- refuse to write into the repository when `--output` starts with the project root;
- never log raw emails;
- write only the requested artifact file;
- return non-zero when duplicate groups exist if `--fail-on-duplicates` is passed.

Use `User.objects.values("normalized_email").annotate(count=Count("id")).filter(count__gt=1)`.

- [ ] **Step 5: Backfill normalized email for test factories**

Update test helpers or `User` manager usage so new users get `normalized_email=normalize_email(email or username).value` during creation. If overriding Django manager is too broad for this task, use `User.save()` only for compatibility read path and keep command-side writes via queryset updates in services.

- [ ] **Step 6: Run tests**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest backend/apps/users/tests/test_email_reconciliation.py backend/apps/users/tests/test_identity_models.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/users/services/email_normalization.py backend/apps/users/management/commands/report_identity_email_duplicates.py backend/apps/users/tests/test_email_reconciliation.py backend/apps/users/models.py
git commit -m "feat(iam): add identity email reconciliation gate"
```

---

### Task 3: Tenant-Free CAS and Invite Token Lifecycle

**Files:**
- Create: `backend/apps/users/repositories/__init__.py`
- Create: `backend/apps/users/repositories/identity_repository.py`
- Create: `backend/apps/users/services/invite_token_service.py`
- Test: `backend/apps/users/tests/test_invite_token_service.py`

**Interfaces:**
- Produces: `IdentityCasRepository.atomic_update(model_class, pk, version, **fields) -> None`
- Produces: `issue_invite(tenant_id, membership_id, ttl) -> InviteTokenIssue`
- Produces: `consume_invite(selector: str, verifier: str) -> TenantMembership`
- Produces: `revoke_invite(invite_id: uuid.UUID, tenant_id: uuid.UUID, expected_version: int) -> None`

- [ ] **Step 1: Write failing tests**

Add `backend/apps/users/tests/test_invite_token_service.py`:

```python
import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.users.models import InviteToken, TenantMembership, User
from apps.users.services.invite_token_service import consume_invite, issue_invite, revoke_invite


@pytest.mark.django_db(transaction=True)
def test_invite_selector_does_not_contain_verifier():
    identity = User.objects.create_user(username="invite@example.com", email="invite@example.com", password="Pass1!")
    tenant_id = uuid.uuid4()
    membership = TenantMembership.objects.create(
        tenant_id=tenant_id,
        identity_id=identity.identity_id,
        email=identity.email,
        status=TenantMembership.Status.INVITED,
    )

    issued = issue_invite(tenant_id=tenant_id, membership_id=membership.id, ttl=timedelta(hours=24))

    assert issued.verifier not in issued.selector
    assert str(tenant_id) in issued.selector
    assert InviteToken.objects.filter(id=issued.invite_id, verifier_hash=issued.verifier_hash).exists()


@pytest.mark.django_db(transaction=True)
def test_consume_invite_sets_consumed_at_once():
    identity = User.objects.create_user(username="consume@example.com", email="consume@example.com", password="Pass1!")
    tenant_id = uuid.uuid4()
    membership = TenantMembership.objects.create(
        tenant_id=tenant_id,
        identity_id=identity.identity_id,
        email=identity.email,
        status=TenantMembership.Status.INVITED,
    )
    issued = issue_invite(tenant_id=tenant_id, membership_id=membership.id, ttl=timedelta(hours=24))

    consumed = consume_invite(selector=issued.selector, verifier=issued.verifier)

    assert consumed.id == membership.id
    token = InviteToken.all_security_objects.get(id=issued.invite_id)
    assert token.consumed_at is not None
    with pytest.raises(PermissionDenied, match="INVITE_ALREADY_CONSUMED"):
        consume_invite(selector=issued.selector, verifier=issued.verifier)


@pytest.mark.django_db(transaction=True)
def test_revoke_invite_blocks_consume_without_soft_delete():
    identity = User.objects.create_user(username="revoke@example.com", email="revoke@example.com", password="Pass1!")
    tenant_id = uuid.uuid4()
    membership = TenantMembership.objects.create(
        tenant_id=tenant_id,
        identity_id=identity.identity_id,
        email=identity.email,
        status=TenantMembership.Status.INVITED,
    )
    issued = issue_invite(tenant_id=tenant_id, membership_id=membership.id, ttl=timedelta(hours=24))
    revoke_invite(invite_id=issued.invite_id, tenant_id=tenant_id, expected_version=1)

    token = InviteToken.all_security_objects.get(id=issued.invite_id)
    assert token.revoked_at is not None
    assert token.is_deleted is False
    with pytest.raises(PermissionDenied, match="INVITE_REVOKED"):
        consume_invite(selector=issued.selector, verifier=issued.verifier)
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest backend/apps/users/tests/test_invite_token_service.py -v
```

Expected: FAIL because repositories and invite service do not exist.

- [ ] **Step 3: Implement tenant-free CAS repository**

Create `backend/apps/users/repositories/identity_repository.py` with:

```python
class IdentityCasConflict(Exception):
    pass


class IdentityCasRepository:
    @staticmethod
    def atomic_update(model_class, *, pk, version: int, **fields) -> None:
        fields["version"] = F("version") + 1
        fields["updated_at"] = timezone.now()
        updated = model_class.objects.filter(pk=pk, version=version, is_legal_hold=False).update(**fields)
        if updated == 0:
            raise IdentityCasConflict("IDENTITY_CAS_CONFLICT")
```

Use the existing tenant-aware repository for tenant records, but create explicit methods for invite consume/revoke so `is_deleted` is never an input.

- [ ] **Step 4: Implement signed selector/verifier service**

Create `backend/apps/users/services/invite_token_service.py`:

- selector payload includes `tenant_id`, `invite_id`, `membership_id`, `key_id`, `issued_at`;
- signature uses Django signing or HMAC with key selected by `key_id`;
- verifier is `secrets.token_urlsafe(32)`;
- DB stores only SHA-256 or Argon2 verifier hash;
- comparison uses `secrets.compare_digest`;
- consume checks `expires_at`, `consumed_at`, `revoked_at`, `version`;
- consume updates `consumed_at` through CAS, not soft delete.

- [ ] **Step 5: Run tests**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest backend/apps/users/tests/test_invite_token_service.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/users/repositories backend/apps/users/services/invite_token_service.py backend/apps/users/tests/test_invite_token_service.py
git commit -m "feat(iam): add invite token security lifecycle"
```

---

### Task 4: Identity Session Service and Refresh Rotation

**Files:**
- Create: `backend/apps/users/services/identity_session_service.py`
- Modify: `backend/apps/users/services/auth_service.py`
- Test: `backend/apps/users/tests/test_identity_session_service.py`

**Interfaces:**
- Produces: `login_with_password(redis_client, email, password, device_metadata) -> LoginResult`
- Produces: `refresh_session(redis_client, refresh_token) -> RefreshResult`
- Produces: `logout_session(redis_client, refresh_token) -> None`
- Produces: `logout_all(identity_id: uuid.UUID) -> int`
- Consumes: `IdentitySession`, `TenantSessionGrant`, `TenantMembership`, `User.identity_id`

- [ ] **Step 1: Write failing tests**

Add `backend/apps/users/tests/test_identity_session_service.py`:

```python
import uuid

import jwt
import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied

from apps.users.models import IdentitySession, TenantMembership, User
from apps.users.services.identity_session_service import login_with_password, logout_all, refresh_session


@pytest.fixture()
def redis():
    return settings.REDIS_CLIENT


@pytest.mark.django_db(transaction=True)
def test_login_issues_identity_session_and_versioned_access_token(redis):
    tenant_id = uuid.uuid4()
    identity = User.objects.create_user(username="login@example.com", email="login@example.com", password="Pass1!")
    membership = TenantMembership.objects.create(
        tenant_id=tenant_id,
        identity_id=identity.identity_id,
        email=identity.email,
        status=TenantMembership.Status.ACTIVE,
    )

    result = login_with_password(redis, email="login@example.com", password="Pass1!", tenant_id=tenant_id, device_metadata={})

    payload = jwt.decode(result.access_token, settings.SECRET_KEY, algorithms=["HS256"])
    assert payload["user_id"] == str(identity.identity_id)
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["membership_id"] == str(membership.id)
    assert payload["identity_security_version"] == identity.security_version
    assert payload["membership_authz_version"] == membership.authz_version
    assert payload["tenant_policy_version"] == 1
    assert payload["grant_context_version"] == 1
    assert IdentitySession.objects.filter(identity_id=identity.identity_id).count() == 1


@pytest.mark.django_db(transaction=True)
def test_refresh_reuse_revokes_session_family(redis):
    tenant_id = uuid.uuid4()
    identity = User.objects.create_user(username="reuse@example.com", email="reuse@example.com", password="Pass1!")
    TenantMembership.objects.create(
        tenant_id=tenant_id,
        identity_id=identity.identity_id,
        email=identity.email,
        status=TenantMembership.Status.ACTIVE,
    )
    login_result = login_with_password(redis, email="reuse@example.com", password="Pass1!", tenant_id=tenant_id, device_metadata={})
    refresh_session(redis, login_result.refresh_token)

    with pytest.raises(PermissionDenied, match="REFRESH_REUSE_DETECTED"):
        refresh_session(redis, login_result.refresh_token)

    assert IdentitySession.objects.get(identity_id=identity.identity_id).revoked_at is not None


@pytest.mark.django_db(transaction=True)
def test_logout_all_increments_security_version_and_revokes_sessions(redis):
    tenant_id = uuid.uuid4()
    identity = User.objects.create_user(username="logoutall@example.com", email="logoutall@example.com", password="Pass1!")
    TenantMembership.objects.create(
        tenant_id=tenant_id,
        identity_id=identity.identity_id,
        email=identity.email,
        status=TenantMembership.Status.ACTIVE,
    )
    login_with_password(redis, email="logoutall@example.com", password="Pass1!", tenant_id=tenant_id, device_metadata={})

    count = logout_all(identity.identity_id)

    identity.refresh_from_db()
    assert count == 1
    assert identity.security_version == 2
    assert IdentitySession.objects.get(identity_id=identity.identity_id).revoked_at is not None
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest backend/apps/users/tests/test_identity_session_service.py -v
```

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Implement session service**

Create `LoginResult` and `RefreshResult` dataclasses with `access_token`, `refresh_token`, `permissions`, `session_id`, `grant_id`, `mfa_required`.

Implement:

- lookup by `normalized_email`;
- uniform `INVALID_CREDENTIALS` for missing user or bad password;
- active membership check;
- `IdentitySession` creation with `session_family_id`;
- refresh token DB/Redis state using `session_id`, `family_id`, `jti`;
- refresh reuse detection that revokes the session family;
- `logout_all` increments `User.security_version` and revokes all sessions.

- [ ] **Step 4: Keep compatibility wrapper**

Modify `backend/apps/users/services/auth_service.py` so existing `login(redis_client, username, password, device_fingerprint)` delegates to `login_with_password(...)` when `username` is an email and falls back to legacy only during mixed rollout. Mark `_pk_to_uuid4()` as compatibility-only and stop using it for new JWT `user_id`.

- [ ] **Step 5: Run tests**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest backend/apps/users/tests/test_identity_session_service.py backend/apps/users/tests/test_auth_service.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/users/services/identity_session_service.py backend/apps/users/services/auth_service.py backend/apps/users/tests/test_identity_session_service.py backend/apps/users/tests/test_auth_service.py
git commit -m "feat(iam): add identity session refresh rotation"
```

---

### Task 5: RLS-Safe Tenant Grant Switch Repository

**Files:**
- Create: `backend/apps/users/repositories/tenant_grant_switch_repository.py`
- Modify: `backend/apps/users/services/identity_session_service.py`
- Test: `backend/apps/users/tests/test_tenant_grant_switch_repository.py`

**Interfaces:**
- Produces: `TenantGrantSwitchRepository.switch(session_id, target_tenant_id, operation_id) -> TenantSessionGrant`
- Consumes: `IdentitySession`, `TenantSessionGrant`, `TenantMembership`, `SessionReconciliationRecord`

- [ ] **Step 1: Write failing tests**

Add `backend/apps/users/tests/test_tenant_grant_switch_repository.py`:

```python
import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.users.models import IdentitySession, SessionReconciliationRecord, TenantMembership, TenantSessionGrant, User
from apps.users.repositories.tenant_grant_switch_repository import TenantGrantSwitchRepository


def _identity_with_memberships():
    identity = User.objects.create_user(username=f"{uuid.uuid4()}@example.com", email=f"{uuid.uuid4()}@example.com", password="Pass1!")
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    membership_a = TenantMembership.objects.create(tenant_id=tenant_a, identity_id=identity.identity_id, email=identity.email, status=TenantMembership.Status.ACTIVE)
    membership_b = TenantMembership.objects.create(tenant_id=tenant_b, identity_id=identity.identity_id, email=identity.email, status=TenantMembership.Status.ACTIVE)
    session = IdentitySession.objects.create(identity_id=identity.identity_id, expires_at=timezone.now() + timedelta(days=7))
    return identity, tenant_a, tenant_b, membership_a, membership_b, session


@pytest.mark.django_db(transaction=True)
def test_switch_tenant_a_to_b_supersedes_a_and_creates_b():
    identity, tenant_a, tenant_b, membership_a, membership_b, session = _identity_with_memberships()
    old = TenantSessionGrant.objects.create(tenant_id=tenant_a, session_id=session.id, membership_id=membership_a.id, context_version=1, issued_at=timezone.now(), expires_at=timezone.now() + timedelta(hours=8))

    new = TenantGrantSwitchRepository.switch(session_id=session.id, target_tenant_id=tenant_b, operation_id=uuid.uuid4())

    old.refresh_from_db()
    assert old.superseded_at is not None
    assert new.tenant_id == tenant_b
    assert new.membership_id == membership_b.id
    assert new.context_version == 2


@pytest.mark.django_db(transaction=True)
def test_switch_supersedes_expired_open_grant_before_insert():
    identity, tenant_a, tenant_b, membership_a, membership_b, session = _identity_with_memberships()
    old = TenantSessionGrant.objects.create(tenant_id=tenant_a, session_id=session.id, membership_id=membership_a.id, context_version=1, issued_at=timezone.now() - timedelta(days=2), expires_at=timezone.now() - timedelta(days=1))

    new = TenantGrantSwitchRepository.switch(session_id=session.id, target_tenant_id=tenant_b, operation_id=uuid.uuid4())

    old.refresh_from_db()
    assert old.superseded_at is not None
    assert new.tenant_id == tenant_b


@pytest.mark.django_db(transaction=True)
def test_idempotent_replay_returns_existing_grant_without_incrementing_context():
    identity, tenant_a, tenant_b, membership_a, membership_b, session = _identity_with_memberships()
    operation_id = uuid.uuid4()

    first = TenantGrantSwitchRepository.switch(session_id=session.id, target_tenant_id=tenant_a, operation_id=operation_id)
    second = TenantGrantSwitchRepository.switch(session_id=session.id, target_tenant_id=tenant_a, operation_id=operation_id)

    assert first.id == second.id
    assert second.context_version == 1
    assert TenantSessionGrant.objects.filter(session_id=session.id, superseded_at__isnull=True, revoked_at__isnull=True).count() == 1


@pytest.mark.django_db(transaction=True)
def test_disabled_target_membership_fails_closed():
    identity, tenant_a, tenant_b, membership_a, membership_b, session = _identity_with_memberships()
    TenantMembership.objects.filter(id=membership_b.id).update(status=TenantMembership.Status.DISABLED)

    with pytest.raises(PermissionDenied, match="MEMBERSHIP_DISABLED"):
        TenantGrantSwitchRepository.switch(session_id=session.id, target_tenant_id=tenant_b, operation_id=uuid.uuid4())


@pytest.mark.django_db(transaction=True)
def test_failure_after_supersede_creates_reconciliation_record(monkeypatch):
    identity, tenant_a, tenant_b, membership_a, membership_b, session = _identity_with_memberships()
    TenantSessionGrant.objects.create(tenant_id=tenant_a, session_id=session.id, membership_id=membership_a.id, context_version=1, issued_at=timezone.now(), expires_at=timezone.now() + timedelta(hours=8))

    monkeypatch.setattr(TenantGrantSwitchRepository, "_create_new_grant", staticmethod(lambda **kwargs: (_ for _ in ()).throw(RuntimeError("insert failed"))))

    with pytest.raises(PermissionDenied, match="TENANT_SWITCH_RECONCILIATION_REQUIRED"):
        TenantGrantSwitchRepository.switch(session_id=session.id, target_tenant_id=tenant_b, operation_id=uuid.uuid4())

    assert SessionReconciliationRecord.objects.filter(session_id=session.id, resolved_at__isnull=True).exists()
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest backend/apps/users/tests/test_tenant_grant_switch_repository.py -v
```

Expected: FAIL because repository does not exist.

- [ ] **Step 3: Implement repository**

Implement `TenantGrantSwitchRepository.switch()`:

- acquire `select_for_update()` lock on `IdentitySession`;
- find open grant with `superseded_at IS NULL` and `revoked_at IS NULL`;
- set old tenant context before locking/superseding old grant;
- switch context to target tenant before membership lookup and new grant insert;
- check membership `identity_id` and `status`;
- update existing open grant `superseded_at=now()` even when expired;
- create new grant with context version `old.context_version + 1` or `1`;
- store `operation_id` on grant or reconciliation record for idempotent replay;
- persist `SessionReconciliationRecord` on ambiguous failure;
- never expose generic `objects.all()` all-tenant access outside this repository.

- [ ] **Step 4: Wire service**

Modify `identity_session_service.login_with_password()` and `refresh_session()` so auto-select and refresh restoration call `TenantGrantSwitchRepository.switch()` instead of inserting grants directly.

- [ ] **Step 5: Run tests**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest backend/apps/users/tests/test_tenant_grant_switch_repository.py backend/apps/users/tests/test_identity_session_service.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/users/repositories/tenant_grant_switch_repository.py backend/apps/users/services/identity_session_service.py backend/apps/users/tests/test_tenant_grant_switch_repository.py
git commit -m "feat(iam): add rls safe tenant grant switching"
```

---

### Task 6: MFA Credentials, Backup Codes and Recovery Tokens

**Files:**
- Modify: `backend/apps/users/services/mfa_service.py`
- Create: `backend/apps/users/services/recovery_service.py`
- Test: `backend/apps/users/tests/test_identity_mfa_recovery.py`

**Interfaces:**
- Produces: `setup_totp(identity_id) -> MfaSetupResult`
- Produces: `confirm_totp(identity_id, code) -> None`
- Produces: `generate_backup_codes(identity_id) -> list[str]`
- Produces: `start_password_recovery(email) -> RecoveryIssueResult`
- Produces: `consume_password_recovery(selector, verifier, new_password) -> None`

- [ ] **Step 1: Write failing tests**

Add `backend/apps/users/tests/test_identity_mfa_recovery.py`:

```python
import pyotp
import pytest
from django.core.exceptions import PermissionDenied

from apps.users.models import BackupCode, MfaCredential, User
from apps.users.services.mfa_service import confirm_totp, generate_backup_codes, setup_totp, verify_backup_code
from apps.users.services.recovery_service import consume_password_recovery, start_password_recovery


@pytest.mark.django_db(transaction=True)
def test_totp_secret_is_stored_as_credential_with_key_id():
    identity = User.objects.create_user(username="mfa@example.com", email="mfa@example.com", password="Pass1!")

    setup = setup_totp(identity.identity_id)

    assert setup.secret
    credential = MfaCredential.objects.get(identity_id=identity.identity_id)
    assert credential.key_id
    assert credential.encrypted_secret != setup.secret
    assert credential.confirmed_at is None


@pytest.mark.django_db(transaction=True)
def test_confirm_totp_increments_security_version():
    identity = User.objects.create_user(username="confirm@example.com", email="confirm@example.com", password="Pass1!")
    setup = setup_totp(identity.identity_id)
    code = pyotp.TOTP(setup.secret).now()

    confirm_totp(identity.identity_id, code)

    identity.refresh_from_db()
    credential = MfaCredential.objects.get(identity_id=identity.identity_id)
    assert credential.confirmed_at is not None
    assert identity.security_version == 2


@pytest.mark.django_db(transaction=True)
def test_backup_code_is_one_time_use_and_hashed():
    identity = User.objects.create_user(username="backup@example.com", email="backup@example.com", password="Pass1!")
    codes = generate_backup_codes(identity.identity_id)

    assert BackupCode.objects.filter(identity_id=identity.identity_id).count() == len(codes)
    assert codes[0] not in BackupCode.objects.values_list("code_hash", flat=True)
    verify_backup_code(identity.identity_id, codes[0])
    with pytest.raises(PermissionDenied, match="BACKUP_CODE_USED"):
        verify_backup_code(identity.identity_id, codes[0])


@pytest.mark.django_db(transaction=True)
def test_password_recovery_uses_selector_verifier_and_revokes_sessions():
    identity = User.objects.create_user(username="recover@example.com", email="recover@example.com", password="OldPass1!")
    issued = start_password_recovery("recover@example.com")

    assert issued.verifier not in issued.selector
    consume_password_recovery(issued.selector, issued.verifier, "NewPass1!")

    identity.refresh_from_db()
    assert identity.check_password("NewPass1!")
    assert identity.security_version == 2
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest backend/apps/users/tests/test_identity_mfa_recovery.py -v
```

Expected: FAIL because identity MFA/recovery service functions do not exist.

- [ ] **Step 3: Implement MFA credential service**

Move TOTP secret authority from `User.mfa_secret` to `MfaCredential.encrypted_secret`. Use `key_id` from settings, and store pending credentials until confirmation. Keep legacy `User.mfa_enabled` updated only as compatibility shadow during mixed rollout.

- [ ] **Step 4: Implement backup codes**

Generate 10 random backup codes, store only hashes with `key_id`, and mark `used_at` through CAS on successful verification.

- [ ] **Step 5: Implement recovery service**

Use the same split selector/verifier pattern as invites. Recovery token is global and references `identity_id`. Consuming a password recovery changes `users.User.password`, increments `security_version`, revokes all sessions, and emits `identity.password_reset`.

- [ ] **Step 6: Run tests**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest backend/apps/users/tests/test_identity_mfa_recovery.py backend/apps/users/tests/test_mfa_service.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/users/services/mfa_service.py backend/apps/users/services/recovery_service.py backend/apps/users/tests/test_identity_mfa_recovery.py backend/apps/users/tests/test_mfa_service.py
git commit -m "feat(iam): move mfa and recovery to identity credentials"
```

---

### Task 7: API, JWT Version Validation and OpenAPI Contract

**Files:**
- Modify: `backend/apps/users/api.py`
- Modify: `backend/apps/users/schemas/auth.py`
- Modify: `backend/apps/common/api/auth.py`
- Modify: `backend/apps/common/middleware/idempotency.py`
- Test: `backend/apps/users/tests/test_identity_auth_api.py`

**Interfaces:**
- Produces: `POST /api/v1/auth/login/` accepting email/password.
- Produces: `POST /api/v1/auth/tenant/select/` requiring idempotent operation handling.
- Produces: `POST /api/v1/auth/logout-all/`.
- Produces: `GET /api/v1/auth/sessions/`.
- Produces: recovery and MFA endpoints backed by identity services.

- [ ] **Step 1: Write failing API tests**

Add `backend/apps/users/tests/test_identity_auth_api.py`:

```python
import uuid

import jwt
import pytest
from django.conf import settings
from ninja.testing import TestClient

from apps.users.api import router
from apps.users.models import TenantMembership, User


@pytest.fixture()
def client():
    return TestClient(router)


@pytest.mark.django_db(transaction=True)
def test_login_response_contains_versioned_access_token(client):
    tenant_id = uuid.uuid4()
    identity = User.objects.create_user(username="api@example.com", email="api@example.com", password="Pass1!")
    TenantMembership.objects.create(tenant_id=tenant_id, identity_id=identity.identity_id, email=identity.email, status=TenantMembership.Status.ACTIVE)

    response = client.post("/login", json={"email": "api@example.com", "password": "Pass1!", "tenant_id": str(tenant_id)})

    assert response.status_code == 200
    payload = jwt.decode(response.json()["access_token"], settings.SECRET_KEY, algorithms=["HS256"])
    assert payload["user_id"] == str(identity.identity_id)
    assert payload["identity_security_version"] == 1
    assert payload["grant_context_version"] == 1


@pytest.mark.django_db(transaction=True)
def test_tenant_select_requires_idempotency_key(client):
    response = client.post("/tenant/select/", json={"tenant_id": str(uuid.uuid4())})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MISSING_IDEMPOTENCY_KEY"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest backend/apps/users/tests/test_identity_auth_api.py -v
```

Expected: FAIL because schemas/routes still use legacy username contract and tenant select is absent.

- [ ] **Step 3: Update schemas**

In `backend/apps/users/schemas/auth.py`, add:

```python
class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1)
    tenant_id: str | None = None
    device_fingerprint: str = Field(default="", max_length=128)


class TenantSelectRequest(BaseModel):
    tenant_id: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    mfa_required: bool = False
    permissions: list[str] = []
    active_tenant_id: str | None = None
    session_id: str | None = None
```

Keep `username` accepted only through a compatibility alias if existing frontend still posts it.

- [ ] **Step 4: Update auth API**

Wire API endpoints to `identity_session_service`, `TenantGrantSwitchRepository`, `mfa_service`, and `recovery_service`. Implement tenant select idempotency inside the auth service because `IdempotencyEnforcementMiddleware` exempts `/api/v1/auth/`.

- [ ] **Step 5: Validate JWT versions in `JWTAuth`**

Modify `backend/apps/common/api/auth.py`:

- parse `user_id` as `identity_id`;
- require `tenant_id`, `membership_id`, `session_id`, `grant_id`;
- check identity security version;
- check membership authz version;
- check tenant policy version;
- check grant context version and open authorization-active state;
- return `None` on mismatch so protected requests fail auth.

- [ ] **Step 6: Run OpenAPI and API tests**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest backend/apps/users/tests/test_identity_auth_api.py backend/apps/users/tests/test_auth_service.py -v
make generate-sdk
make tsc
```

Expected: PASS; generated SDK changes are committed, manual SDK edits are not made.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/users/api.py backend/apps/users/schemas/auth.py backend/apps/common/api/auth.py backend/apps/common/middleware/idempotency.py backend/apps/users/tests/test_identity_auth_api.py frontend/src/shared/api/generated
git commit -m "feat(iam): expose identity auth api contract"
```

---

### Task 8: Backfill, Contract Readiness and Stabilization Gate

**Files:**
- Create: `backend/apps/users/management/commands/backfill_identity_memberships.py`
- Create: `backend/apps/users/tests/test_identity_backfill.py`
- Modify: `docs/adr/0024-global-identity-tenant-membership.md`
- Modify: `docs/superpowers/plans/2026-07-30-iam-identity.md`

**Interfaces:**
- Produces: `backfill_identity_memberships --batch-size 5000 --dry-run`
- Produces: rollout gate that blocks contract phase when duplicate email report is non-empty.
- Consumes: all models and services from previous tasks.

- [ ] **Step 1: Write failing backfill tests**

Add `backend/apps/users/tests/test_identity_backfill.py`:

```python
import uuid

import pytest
from django.core.management import call_command

from apps.users.models import TenantMembership, User


@pytest.mark.django_db(transaction=True)
def test_backfill_creates_membership_from_legacy_user_tenant_id():
    tenant_id = uuid.uuid4()
    user = User.objects.create_user(username="legacy@example.com", email="legacy@example.com", password="Pass1!", tenant_id=tenant_id)

    call_command("backfill_identity_memberships", "--batch-size", "5000")

    membership = TenantMembership.objects.get(identity_id=user.identity_id)
    assert membership.tenant_id == tenant_id
    assert membership.email == "legacy@example.com"
    assert membership.status == TenantMembership.Status.ACTIVE


@pytest.mark.django_db(transaction=True)
def test_backfill_dry_run_does_not_write_memberships():
    tenant_id = uuid.uuid4()
    user = User.objects.create_user(username="dry@example.com", email="dry@example.com", password="Pass1!", tenant_id=tenant_id)

    call_command("backfill_identity_memberships", "--dry-run")

    assert not TenantMembership.objects.filter(identity_id=user.identity_id).exists()
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest backend/apps/users/tests/test_identity_backfill.py -v
```

Expected: FAIL because backfill command does not exist.

- [ ] **Step 3: Implement backfill command**

Create `backfill_identity_memberships`:

- process batches of 5000 users;
- use `select_for_update(skip_locked=True)`;
- create one membership from legacy `User.tenant_id` only when membership is missing;
- never log raw email;
- support `--dry-run`;
- support `--tenant-id` for limited rollout;
- exit non-zero if duplicate normalized email report has unresolved groups.

- [ ] **Step 4: Document release gates**

Update ADR or add a short appendix in this plan with exact gates:

- Release N: schema and dual-read code deployed.
- Release N+1: duplicate report clean, backfill completed, auth dual-read metrics clean.
- Release N+2: contract removes `users.User.tenant_id` authority and legacy MFA authority.

- [ ] **Step 5: Run stabilization checks**

Run:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest backend/apps/users/tests -v
make test-common
make generate-sdk
make tsc
git diff --check
```

Expected: PASS. If `make test-common` exposes unrelated failures, record exact failing tests and rerun the smallest IAM-specific suite before handoff.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/users/management/commands/backfill_identity_memberships.py backend/apps/users/tests/test_identity_backfill.py docs/adr/0024-global-identity-tenant-membership.md docs/superpowers/plans/2026-07-30-iam-identity.md
git commit -m "feat(iam): add identity backfill rollout gate"
```

---

## Final Acceptance

- `User.identity_id` exists, is stable, unique and used in new JWTs.
- `AUTH_USER_MODEL` is still `users.User`.
- `User.password` remains the password hash authority.
- `TenantMembership` is the tenant-local employee authority.
- `IdentitySession` and `TenantSessionGrant` replace Redis-only session authority.
- Expired open grants are superseded before any new grant insert.
- `TenantGrantSwitchRepository` is the only cross-tenant grant switch path.
- `InviteToken` and `TenantSessionGrant` validity never depends on generic soft delete.
- Duplicate normalized emails produce a protected reconciliation report and block contract.
- Final email uniqueness is `UNIQUE(normalized_email)`.
- JWT version mismatches fail closed.
- Refresh reuse revokes the session family and emits `auth.refresh_reuse_detected`.
- Password recovery and MFA changes increment `security_version` and revoke sessions as specified.
- OpenAPI/SDK generation and TypeScript checks pass after API changes.

## Rollback

- Before Release N+2, rollback by disabling identity-session issuing and returning to legacy `auth_service` paths while keeping added tables.
- If grant switch reconciliation errors increase, disable tenant select endpoint, revoke affected sessions, and keep login single-tenant auto-select disabled until reconciliation records are resolved.
- If duplicate email reconciliation finds unresolved groups, stop before adding `UNIQUE(normalized_email)` and keep normalized email nullable until manual resolution is complete.
- If JWT version validation blocks valid users during rollout, switch validation to LOG_ONLY for one release while preserving refresh/session revocation checks, then fix version source mismatch before ENFORCE.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-30-iam-identity.md`. Two execution options:

1. Subagent-Driven - dispatch a fresh worker per task and review between tasks.
2. Inline Execution - execute tasks in this session using checkpoints after each task.
