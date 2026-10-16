"""Small IAM fixtures that preserve the tenant-local actor boundary."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass

from django.contrib.auth.hashers import make_password

from apps.common.models.rbac import Role, RoleBinding
from apps.common.tenancy.context import tenant_rls_scope
from apps.users.models import TenantMembership, User
from apps.users.repositories.membership_repository import MembershipRepository
from apps.users.services.auth_service import _pk_to_uuid4
from apps.users.services.email_normalization import normalize_email


def make_compat_user(**kwargs) -> User:
    tenant_id = kwargs.pop("tenant_id", uuid.uuid4())
    identity_id = kwargs.pop("identity_id", uuid.uuid4())
    password = kwargs.pop("password", secrets.token_urlsafe(24))
    email = kwargs.setdefault("email", f"fixture-{identity_id.hex}@example.invalid")
    kwargs.setdefault("username", email)
    normalized = normalize_email(email)
    user = User.objects.create(
        identity_id=identity_id,
        normalized_email=normalized.value,
        email_normalization_version=normalized.version,
        security_version=1,
        security_status=User.SecurityStatus.ACTIVE,
        identity_legal_hold=False,
        password=make_password(password),
        **kwargs,
    )
    user._fixture_password = password
    MembershipRepository.ensure_foundation(
        tenant_id=tenant_id,
        identity_id=identity_id,
        legacy_actor_user_id=user.pk,
        display_name=user.get_full_name() or user.username,
    )
    return user


def make_membership(*, tenant_id: uuid.UUID, identity: User, actor: User) -> TenantMembership:
    return MembershipRepository.ensure_foundation(
        tenant_id=tenant_id,
        identity_id=identity.identity_id,
        legacy_actor_user_id=actor.pk,
        display_name=actor.get_full_name() or actor.username,
    )


def make_role_binding(*, tenant_id: uuid.UUID, actor: User, role: Role) -> RoleBinding:
    with tenant_rls_scope(tenant_id):
        return RoleBinding.objects.create(
            tenant_id=tenant_id,
            user_id=_pk_to_uuid4(actor.pk),
            role=role,
            is_active=True,
        )


@dataclass(frozen=True)
class TwoMemberships:
    identity: User
    tenant_a: uuid.UUID
    tenant_b: uuid.UUID
    membership_a: TenantMembership
    membership_b: TenantMembership
