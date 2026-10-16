"""Explicit local operator boundary for disposable Files/RBAC accounts."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from apps.common.models.rbac import Role, RoleBinding
from apps.common.repositories.local_rbac_repository import local_actor_has_exact_profile
from apps.common.services.local_rbac_service import ensure_local_files_profile
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.tenancy.context import tenant_rls_scope
from apps.users.events import IdentityCreatedEvent
from apps.users.models import IdentitySecurityAuditRecord, TenantMembership, User
from apps.users.repositories.identity_creation_repository import create_identity
from apps.users.repositories.membership_repository import MembershipRepository
from apps.users.services.email_normalization import normalize_email
from apps.users.services.user_admin_service import _to_uuid

_operator: ContextVar[str | None] = ContextVar("local_provisioning_operator", default=None)
PUBLIC_SITE_TENANT_ID = UUID("a1b2c3d4-e5f6-4a7b-8c9d-e0f1a2b3c4d5")
_TEST_USERS = (
    ("dir_test", "dir_test@test.com", "director"),
    ("mgr_test", "mgr_test@test.com", "manager"),
    ("acc_test", "acc_test@test.com", "accountant"),
    ("foreman_test", "foreman_test@test.com", "foreman"),
)


@dataclass(frozen=True)
class ProvisionedLocalAccount:
    user_id: int
    tenant_id: UUID
    email: str
    created: bool


def require_local_environment(expected_database: str) -> None:
    if not (
        settings.DEBUG
        and os.environ.get("ENVIRONMENT") == "local"
        and os.environ.get("DJANGO_SETTINGS_MODULE") == "config.settings.local"
        and getattr(settings, "SETTINGS_MODULE", None) == "config.settings.local"
        and connection.settings_dict.get("HOST") == "postgres_primary"
    ):
        raise ValidationError("LOCAL_ENVIRONMENT_REQUIRED")
    if not expected_database or connection.settings_dict.get("NAME") != expected_database:
        raise ValidationError("LOCAL_DATABASE_MISMATCH")
    state_dir = Path(os.environ.get("DEV_STATE_DIR", "/var/lib/complect-dev"))
    receipt = state_dir / "schema.json"
    try:
        ready = receipt.is_file() and json.loads(receipt.read_text()).get("state") == "ready"
    except (OSError, ValueError):
        ready = False
    if not ready:
        raise ValidationError("LOCAL_MANAGED_DATABASE_REQUIRED")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_database(), rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        database, superuser, bypassrls = cursor.fetchone()
        if database != expected_database or superuser or bypassrls:
            raise ValidationError("LOCAL_RESTRICTED_DATABASE_REQUIRED")
        cursor.execute(
            "SELECT count(*) FROM pg_class WHERE relname IN "
            "('users_tenant_memberships', 'users_tenant_authorization_state', "
            "'users_tenant_session_grants') AND relrowsecurity AND relforcerowsecurity"
        )
        if cursor.fetchone()[0] != 3:
            raise ValidationError("LOCAL_FORCE_RLS_REQUIRED")


def require_local_operator(operator_ref: str | None) -> None:
    if not operator_ref or _operator.get() != operator_ref:
        raise ValidationError("LOCAL_OPERATOR_BOUNDARY_REQUIRED")
    if operator_ref != f"local-cli:uid:{os.getuid()}":
        raise ValidationError("LOCAL_OPERATOR_REQUIRED")


@contextmanager
def local_operator_scope(operator_ref: str):
    token = _operator.set(operator_ref)
    try:
        require_local_operator(operator_ref)
        yield
    finally:
        _operator.reset(token)


def _record_creation(user: User, tenant_id: UUID, operator_ref: str) -> None:
    identity_id = user.identity_id
    if identity_id is None:
        raise ValidationError("LOCAL_IDENTITY_REQUIRED")
    correlation_id = uuid4()
    occurred_at = timezone.now()
    IdentitySecurityAuditRecord.objects.create(
        identity_id=identity_id,
        event_type="identity.created",
        actor_type="operator",
        actor_ref=operator_ref,
        reason_code="LOCAL_TEST_USER_CREATED",
        subject_hmac="",
        request_id=uuid4(),
        correlation_id=correlation_id,
        metadata={"tenant_id": str(tenant_id), "local_profile": "local_files_e2e"},
        occurred_at=occurred_at,
    )
    with tenant_rls_scope(tenant_id):
        TransactionalOutboxService.push_to_outbox(
            IdentityCreatedEvent(
                event_id=uuid4(),
                event_version=1,
                occurred_at=occurred_at,
                correlation_id=correlation_id,
                identity_id=identity_id,
                tenant_id=tenant_id,
            )
        )


def _create_account(
    *, tenant_id: UUID, username: str, email: str, password: str, role_name: str, operator_ref: str
) -> User:
    normalized = normalize_email(email)
    user = create_identity(
        username=username,
        email=email,
        tenant_id=tenant_id,
        password=make_password(password),
        identity_id=uuid4(),
        normalized_email=normalized.value,
        email_normalization_version=normalized.version,
        security_version=1,
        security_status=User.SecurityStatus.MFA_SETUP_REQUIRED,
        identity_legal_hold=False,
        is_active=True,
    )
    MembershipRepository.ensure_foundation(
        tenant_id=tenant_id,
        identity_id=user.identity_id,
        legacy_actor_user_id=user.pk,
        display_name=username,
    )
    with tenant_rls_scope(tenant_id):
        role = Role.objects.get(name=role_name)
        RoleBinding.objects.create(
            tenant_id=tenant_id,
            user_id=_to_uuid(user.pk),
            role=role,
            granted_by=None,
        )
    _record_creation(user, tenant_id, operator_ref)
    return user


def provision_local_files_account(
    *, expected_database: str, email: str, password: str, operator_ref: str
) -> ProvisionedLocalAccount:
    require_local_environment(expected_database)
    require_local_operator(operator_ref)
    if len(password) < 20:
        raise ValidationError("LOCAL_PASSWORD_LENGTH")
    normalized = normalize_email(email)
    with transaction.atomic():
        tenant_id, new_profile = ensure_local_files_profile()
        existing = User.objects.filter(normalized_email=normalized.value).first()
        if existing is not None:
            if existing.identity_id is None:
                raise ValidationError("LOCAL_ACCOUNT_CONFLICT")
            with tenant_rls_scope(tenant_id):
                membership = TenantMembership.objects.filter(
                    identity_id=existing.identity_id,
                    legacy_actor_user_id=existing.pk,
                    status=TenantMembership.Status.ACTIVE,
                    is_deleted=False,
                ).first()
                valid_binding = membership is not None and local_actor_has_exact_profile(
                    tenant_id, _to_uuid(existing.pk)
                )
            if new_profile or not valid_binding or existing.identity_legal_hold or not existing.is_active:
                raise ValidationError("LOCAL_ACCOUNT_CONFLICT")
            if not existing.check_password(password):
                raise ValidationError("LOCAL_CREDENTIALS_CONFLICT_NO_RESET")
            return ProvisionedLocalAccount(existing.pk, tenant_id, existing.email, False)
        if not new_profile:
            raise ValidationError("LOCAL_ACCOUNT_CONFLICT")
        token = _operator.set(operator_ref)
        try:
            user = _create_account(
                tenant_id=tenant_id,
                username=email,
                email=email,
                password=password,
                role_name="local_files_e2e",
                operator_ref=operator_ref,
            )
        finally:
            _operator.reset(token)
    return ProvisionedLocalAccount(user.pk, tenant_id, user.email, True)


def provision_local_test_users(*, expected_database: str) -> tuple[int, int]:
    require_local_environment(expected_database)
    password = os.environ.get("COMPLECT_LOCAL_TEST_PASSWORD")
    if not password or len(password) < 20:
        raise ValidationError("LOCAL_TEST_PASSWORD_REQUIRED")
    operator_ref = f"local-cli:uid:{os.getuid()}"
    token = _operator.set(operator_ref)
    try:
        with transaction.atomic():
            tenant_id, _ = ensure_local_files_profile()
            roles = set(Role.objects.filter(name__in=[row[2] for row in _TEST_USERS]).values_list("name", flat=True))
            missing = sorted({row[2] for row in _TEST_USERS} - roles)
            if missing:
                raise ValidationError(f"UNKNOWN_ROLE:{missing[0]}")
            created = 0
            existing = 0
            for username, email, role_name in _TEST_USERS:
                user = User.objects.filter(username=username).first()
                if user is not None:
                    valid_public_membership = False
                    if user.email == email and user.identity_id is not None and not user.identity_legal_hold:
                        with tenant_rls_scope(PUBLIC_SITE_TENANT_ID):
                            membership = TenantMembership.objects.filter(
                                tenant_id=PUBLIC_SITE_TENANT_ID,
                                identity_id=user.identity_id,
                                legacy_actor_user_id=user.pk,
                                status=TenantMembership.Status.ACTIVE,
                                is_deleted=False,
                            ).first()
                            valid_public_membership = membership is not None
                    if not valid_public_membership:
                        raise ValidationError(f"FIXED_TEST_USER_CONFLICT:{username}")
                    existing += 1
                    continue
                _create_account(
                    tenant_id=PUBLIC_SITE_TENANT_ID,
                    username=username,
                    email=email,
                    password=password,
                    role_name=role_name,
                    operator_ref=operator_ref,
                )
                created += 1
            return created, existing
    finally:
        _operator.reset(token)
