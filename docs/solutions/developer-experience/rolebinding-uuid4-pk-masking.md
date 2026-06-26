---
title: "RoleBinding UUID Mismatch: _pk_to_uuid4 Encodes Integer PKs with UUID4 Version Bits"
date: "2026-06-26"
category: developer-experience
module: apps/users/services
problem_type: developer_experience
component: authentication
severity: high
applies_when:
  - "Manually creating RoleBindings in the DB shell or management commands after a DB reset"
  - "Seeding test users that need RBAC permissions (superusers still require a RoleBinding)"
  - "Any code that maps an integer User PK to a UUID for RoleBinding lookup"
tags:
  - rbac
  - rolebinding
  - uuid
  - pk-masking
  - db-reset
  - seed-data
  - permissions
  - authentication
---

# RoleBinding UUID Mismatch: _pk_to_uuid4 Encodes Integer PKs with UUID4 Version Bits

## Context

After a full DB drop/recreate, testadmin (Django superuser, `pk=1`) had no navigation items — the
sidebar showed only "Сводка". Diagnosis: `_get_user_permissions()` returned `[]` because the
manually created `RoleBinding` used a simple zero-padded UUID
(`00000000-0000-0000-0000-000000000001`) while the auth service queries with
`_pk_to_uuid4(user.pk)` which returns a UUID4-encoded form with version bits set.

This silently produces 0 permissions with no error. Django superusers receive no special treatment
in `_get_user_permissions` — they must have a RoleBinding like any other user.

## Guidance

When creating a `RoleBinding` for any user in the DB shell, a management command, or seed data,
always compute the `user_id` via `_pk_to_uuid4(user.pk)`:

```python
# In the Django shell (docker compose exec backend python manage.py shell):
import uuid
from apps.users.services.auth_service import _pk_to_uuid4
from apps.common.models.rbac import RoleBinding, Role

user_pk = 1   # testadmin's integer PK
correct_uuid = _pk_to_uuid4(user_pk)
# → UUID('00000000-0000-4000-8000-000000000001')

tenant_id = uuid.UUID('a1b2c3d4-e5f6-4a7b-8c9d-e0f1a2b3c4d5')  # test tenant

RoleBinding.objects.create(
    user_id=correct_uuid,
    tenant_id=tenant_id,
    role=Role.objects.get(name='admin'),
    is_active=True,
)
```

**The function** (`apps/users/services/auth_service.py`):

```python
def _pk_to_uuid4(pk: int) -> uuid.UUID:
    """Encode integer PK as UUID4: sets version=4, variant=RFC4122."""
    b = pk.to_bytes(16, "big")
    b = b[:6] + bytes([b[6] & 0x0F | 0x40]) + bytes([b[7] & 0x3F | 0x80]) + b[8:]
    return uuid.UUID(bytes=b)
```

`_pk_to_uuid4(1)` → `00000000-0000-4000-8000-000000000001` (NOT `00000000-0000-0000-0000-000000000001`)

## Why This Matters

`_get_user_permissions(user)` in `auth_service.py` calls:

```python
RoleBinding.objects.filter(
    user_id=_pk_to_uuid4(user.pk),  # <— UUID4-encoded, not zero-padded
    tenant_id=user.tenant_id,
    is_active=True,
)
```

A binding created with a plain zero-padded UUID will **never match** this filter. The result is
`[]` permissions, causing all RBAC-guarded navigation items to be hidden in the frontend
(`useCan()` in `layout.tsx` returns `false` for every permission). The superuser still logs in
successfully — the absence of permissions is silent on the backend side.

**Django `is_superuser` is NOT a bypass** for `_get_user_permissions`. The function checks
RoleBindings only, regardless of superuser status.

## When to Apply

- After every DB reset/recreate where users are seeded from scratch
- In the `create_test_users` management command when creating users with RBAC roles
- In any migration that seeds RoleBindings for initial admin users
- When debugging "no nav items / empty sidebar" after DB changes

## Examples

**Wrong (zero-padded — causes 0 permissions):**

```python
# WRONG: simple zero-padding
user_id = uuid.UUID('00000000-0000-0000-0000-000000000001')
RoleBinding.objects.create(user_id=user_id, ...)
```

**Correct (UUID4-encoded via the helper):**

```python
from apps.users.services.auth_service import _pk_to_uuid4

user_id = _pk_to_uuid4(1)  # → 00000000-0000-4000-8000-000000000001
RoleBinding.objects.create(user_id=user_id, ...)
```

**Verification after creating a RoleBinding:**

```python
from django.contrib.auth import get_user_model
from apps.users.services.auth_service import _get_user_permissions

User = get_user_model()
user = User.objects.get(username='testadmin')
perms = _get_user_permissions(user)
print(f"{len(perms)} permissions")  # must be > 0
```

## Related

- `apps/users/services/auth_service.py` — `_pk_to_uuid4`, `_get_user_permissions`
- `apps/common/models/rbac.py` — `RoleBinding`, `Role`
- `docs/solutions/developer-experience/admin-user-creation-temp-password-flow.md` — related DB
  reset operational guide
