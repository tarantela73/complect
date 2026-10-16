"""Creation-only persistence for an explicitly owned local test profile."""
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import connection

from apps.common.models.rbac import Permission, Role, RoleBinding, RolePermission

PROFILE = 'local_files_e2e'
DESCRIPTION_PREFIX = 'local-files-e2e:v1:'
PERMISSIONS = frozenset({('files', 'read'), ('files', 'create'), ('files', 'delete')})


def lock_local_profile():
    if not connection.in_atomic_block:
        raise RuntimeError('LOCAL_PROFILE_TRANSACTION_REQUIRED')
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(91742, hashtext(%s))", [PROFILE])


def find_local_profile_tenant() -> UUID | None:
    role = Role.objects.select_for_update().filter(name=PROFILE).first()
    if role is None:
        return None
    try:
        if not role.description.startswith(DESCRIPTION_PREFIX):
            raise ValueError
        tenant_id = UUID(role.description[len(DESCRIPTION_PREFIX):])
    except ValueError:
        raise ValidationError('LOCAL_ROLE_CONFLICT') from None
    if set(role.permissions.values_list('resource', 'action')) != PERMISSIONS:
        raise ValidationError('LOCAL_ROLE_CONFLICT')
    return tenant_id


def create_local_profile(tenant_id: UUID):
    permissions = list(Permission.objects.filter(resource='files', action__in=['read', 'create', 'delete']))
    if {(p.resource, p.action) for p in permissions} != PERMISSIONS:
        raise ValidationError('LOCAL_PERMISSIONS_MISSING')
    role = Role.objects.create(name=PROFILE, description=DESCRIPTION_PREFIX + str(tenant_id))
    RolePermission.objects.bulk_create([RolePermission(role=role, permission=p) for p in permissions])


def validate_local_role_tenant(role: Role, tenant_id: UUID):
    if role.name == PROFILE and role.description != DESCRIPTION_PREFIX + str(tenant_id):
        raise ValidationError('LOCAL_ROLE_TENANT_MISMATCH')


def local_actor_has_exact_profile(tenant_id: UUID, actor_uuid: UUID) -> bool:
    return list(RoleBinding.objects.filter(
        tenant_id=tenant_id, user_id=actor_uuid, is_active=True,
    ).values_list('role__name', flat=True)) == [PROFILE]
