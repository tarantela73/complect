from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from apps.common.models.rbac import Role, RoleBinding
from apps.users.models import User
from apps.users.services.auth_service import _pk_to_uuid4


@dataclass
class UserListItem:
    id: int
    user_uuid: str
    email: str
    full_name: str
    is_active: bool
    roles: list[str]
    last_login: str | None
    created_at: str


@dataclass
class RoleDetail:
    name: str
    description: str
    permissions: list[str] = field(default_factory=list)


def list_users(tenant_id: uuid.UUID) -> list[UserListItem]:
    if hasattr(User, "is_deleted"):
        users = User.objects.filter(tenant_id=tenant_id, is_deleted=False).order_by("id")
    else:
        users = User.objects.filter(tenant_id=tenant_id).order_by("id")

    user_pks = [u.pk for u in users]
    uuid_to_pk = {_pk_to_uuid4(pk): pk for pk in user_pks}

    bindings = (
        RoleBinding.objects.filter(
            tenant_id=tenant_id,
            user_id__in=uuid_to_pk.keys(),
            is_active=True,
        )
        .select_related("role")
        .values("user_id", "role__name")
    )

    roles_by_user_uuid: dict[str, list[str]] = {}
    for b in bindings:
        key = str(b["user_id"])
        roles_by_user_uuid.setdefault(key, []).append(b["role__name"])

    result = []
    for u in users:
        user_uuid = _pk_to_uuid4(u.pk)
        result.append(
            UserListItem(
                id=u.pk,
                user_uuid=user_uuid,
                email=u.email,
                full_name=u.first_name or u.username,
                is_active=u.is_active,
                roles=sorted(roles_by_user_uuid.get(user_uuid, [])),
                last_login=u.last_login.isoformat() if u.last_login else None,
                created_at=u.date_joined.isoformat(),
            )
        )
    return result


def list_roles() -> list[RoleDetail]:
    roles = Role.objects.prefetch_related("permissions").order_by("name")
    return [
        RoleDetail(
            name=r.name,
            description=r.description,
            permissions=sorted(f"{p.resource}.{p.action}" for p in r.permissions.all()),
        )
        for r in roles
    ]
