"""
Utilities for user identity resolution.

Django's integer pk is encoded as a deterministic UUID for API surfaces.
The mask extracts the original pk bits. Mirrors the encoding in auth_service._pk_to_uuid4.
"""

import uuid

from django.contrib.auth import get_user_model

_UUID_PK_MASK = 0xFFFFFFFFFFFF


def user_exists_in_tenant(user_uuid: uuid.UUID, tenant_id: uuid.UUID) -> bool:
    """Return True if the user encoded by user_uuid belongs to tenant_id."""
    pk = user_uuid.int & _UUID_PK_MASK
    user_model = get_user_model()
    return user_model.objects.filter(pk=pk, tenant_id=tenant_id).exists()
