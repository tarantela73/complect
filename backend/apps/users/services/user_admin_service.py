"""
User administration service.

Handles: create, disable, enable, assign_roles.
All mutations go through this service — never raw ORM in views.

Security invariants enforced here (service layer, defense-in-depth):
  - CANNOT_DISABLE_SELF: actor cannot disable their own account
  - LAST_ADMIN_LOCK: the sole active admin cannot be disabled or demoted
  - CANNOT_REMOVE_OWN_ADMIN: actor cannot remove their own admin role
  - EMAIL_ALREADY_EXISTS: unique email per Django user table
  - UNKNOWN_ROLE: only seeded role names are valid
"""

from __future__ import annotations

import secrets
import string
import uuid

from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.common.models.rbac import Role, RoleBinding
from apps.users.models import User
from apps.users.services.auth_service import _pk_to_uuid4
from apps.users.services.deferred_merge_guard import DeferredMergeGuard
from apps.users.services.email_normalization import is_reserved_merge_email


def _to_uuid(pk: int) -> uuid.UUID:
    return uuid.UUID(_pk_to_uuid4(pk))


def _active_admin_count(tenant_id: uuid.UUID, exclude_user_id: uuid.UUID | None = None) -> int:
    admin_role = Role.objects.get(name="admin")
    qs = RoleBinding.objects.filter(
        tenant_id=tenant_id,
        role=admin_role,
        is_active=True,
    )
    if exclude_user_id is not None:
        qs = qs.exclude(user_id=exclude_user_id)
    return qs.count()


def _has_admin_role(tenant_id: uuid.UUID, user_uuid: uuid.UUID) -> bool:
    admin_role = Role.objects.get(name="admin")
    return RoleBinding.objects.filter(
        tenant_id=tenant_id,
        user_id=user_uuid,
        role=admin_role,
        is_active=True,
    ).exists()


def _generate_temp_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        # Ensure at least one of each required character class
        if (
            any(c.isupper() for c in pwd)
            and any(c.islower() for c in pwd)
            and any(c.isdigit() for c in pwd)
            and any(c in "!@#$%" for c in pwd)
        ):
            return pwd


def create_user(
    *,
    tenant_id: uuid.UUID,
    email: str,
    full_name: str,
    roles: list[str],
    actor_id: uuid.UUID | None,
    username: str | None = None,
    operator_ref: str | None = None,
    initial_password: str | None = None,
) -> tuple[User, str]:
    """
    Creates a new user within the tenant with a generated temporary password.
    Returns (user, temp_password) — caller is responsible for delivering
    the temp_password to the new user securely (email or admin copy).
    """
    if actor_id is None or operator_ref is not None or initial_password is not None:
        from apps.users.services.local_files_provisioning import require_local_operator

        require_local_operator(operator_ref)
        if actor_id is not None:
            raise ValidationError("LOCAL_OPERATOR_ACTOR_CONFLICT")
    if is_reserved_merge_email(email):
        raise ValidationError({"email": "RESERVED_IDENTITY_EMAIL"}, code="RESERVED_IDENTITY_EMAIL")
    if User.objects.filter(email=email).exists():
        raise ValidationError(
            {"email": "EMAIL_ALREADY_EXISTS"},
            code="EMAIL_ALREADY_EXISTS",
        )

    role_objects: list[Role] = []
    for role_name in roles:
        try:
            role_objects.append(Role.objects.get(name=role_name))
        except Role.DoesNotExist:
            raise ValidationError(
                {"roles": f"UNKNOWN_ROLE:{role_name}"},
                code="UNKNOWN_ROLE",
            ) from None

    temp_password = initial_password or _generate_temp_password()

    with transaction.atomic():
        user = User(
            username=username or email,
            email=email,
            first_name=full_name,
            tenant_id=tenant_id,
            is_active=True,
        )
        user.password = make_password(temp_password)
        user.save()

        user_uuid = _to_uuid(user.pk)
        for role in role_objects:
            RoleBinding.objects.create(
                tenant_id=tenant_id,
                user_id=user_uuid,
                role=role,
                granted_by=actor_id,
            )

    return user, temp_password


def disable_user(
    *,
    tenant_id: uuid.UUID,
    user_id: int,
    actor_id: uuid.UUID,
) -> None:
    user_uuid = _to_uuid(user_id)

    if user_uuid == actor_id:
        raise ValidationError(
            {"user_id": "CANNOT_DISABLE_SELF"},
            code="CANNOT_DISABLE_SELF",
        )

    if _has_admin_role(tenant_id, user_uuid):
        remaining_admins = _active_admin_count(tenant_id, exclude_user_id=user_uuid)
        if remaining_admins == 0:
            raise ValidationError(
                {"user_id": "LAST_ADMIN_LOCK"},
                code="LAST_ADMIN_LOCK",
            )

    User.objects.filter(pk=user_id, tenant_id=tenant_id).update(is_active=False)


def enable_user(
    *,
    tenant_id: uuid.UUID,
    user_id: int,
    actor_id: uuid.UUID,
) -> None:
    try:
        user = User.objects.get(pk=user_id, tenant_id=tenant_id)
    except User.DoesNotExist:
        return
    authority = DeferredMergeGuard.resolve_legacy_actor(user)
    if authority.identity.identity_id:
        DeferredMergeGuard.require_activation_allowed(authority.identity.identity_id)
    if authority.is_alias:
        raise ValidationError(
            {"user_id": "MERGE_SOURCE_COMPATIBILITY_ROW"},
            code="MERGE_SOURCE_COMPATIBILITY_ROW",
        )
    User.objects.filter(pk=user_id, tenant_id=tenant_id).update(is_active=True)


def assign_roles(
    *,
    tenant_id: uuid.UUID,
    user_id: int,
    roles: list[str],
    actor_id: uuid.UUID,
) -> None:
    """
    Atomically replaces all role bindings for the user.
    Old bindings are deactivated; new ones are created.
    Invalidates the RBAC policy cache on commit.
    """
    from apps.common.security.policy_engine import PolicyEngine

    user_uuid = _to_uuid(user_id)

    # Guard: cannot demote last admin
    actor_is_target = user_uuid == actor_id
    target_is_currently_admin = _has_admin_role(tenant_id, user_uuid)
    target_keeps_admin = "admin" in roles

    if target_is_currently_admin and not target_keeps_admin:
        if actor_is_target:
            raise ValidationError(
                {"roles": "CANNOT_REMOVE_OWN_ADMIN"},
                code="CANNOT_REMOVE_OWN_ADMIN",
            )
        remaining_admins = _active_admin_count(tenant_id, exclude_user_id=user_uuid)
        if remaining_admins == 0:
            raise ValidationError(
                {"roles": "LAST_ADMIN_LOCK"},
                code="LAST_ADMIN_LOCK",
            )

    role_objects: list[Role] = []
    for role_name in roles:
        try:
            role_objects.append(Role.objects.get(name=role_name))
        except Role.DoesNotExist:
            raise ValidationError(
                {"roles": f"UNKNOWN_ROLE:{role_name}"},
                code="UNKNOWN_ROLE",
            ) from None

    with transaction.atomic():
        RoleBinding.objects.filter(
            tenant_id=tenant_id,
            user_id=user_uuid,
        ).update(is_active=False)

        for role in role_objects:
            RoleBinding.objects.update_or_create(
                tenant_id=tenant_id,
                user_id=user_uuid,
                role=role,
                defaults={"is_active": True, "granted_by": actor_id},
            )

        transaction.on_commit(lambda: PolicyEngine.invalidate(tenant_id=tenant_id, actor_id=user_uuid))
