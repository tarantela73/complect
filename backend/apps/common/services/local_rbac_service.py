"""Narrow local operator profile; the permission registry remains authoritative."""
from uuid import uuid4

from apps.common.repositories.local_rbac_repository import (
    create_local_profile,
    find_local_profile_tenant,
    lock_local_profile,
)


def ensure_local_files_profile():
    lock_local_profile()
    tenant_id = find_local_profile_tenant()
    if tenant_id is not None:
        return tenant_id, False
    tenant_id = uuid4()
    create_local_profile(tenant_id)
    return tenant_id, True
