---
description: "Setup RBAC RoleBinding for a user with correct _pk_to_uuid4 encoding. Fixes the critical gotcha where user_id in RoleBinding must use encoded UUID, not raw PK."
argument-hint: "[username] [role] [password] (defaults: testadmin admin Admin123!)"
---

# Setup RBAC RoleBinding

The `_pk_to_uuid4` function in `apps/users/services/auth_service.py` encodes integer PKs to deterministic UUID v4. When creating RoleBinding records, you MUST use the encoded UUID.

## Steps

### 1. Check current state

```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py shell -c "
from apps.common.models.rbac import RoleBinding, Role
from apps.users.models import User
import uuid

u = User.objects.get(username='$1')
correct_uuid = uuid.UUID(int=0x00000000000040008000000000000000 | (u.pk & 0xFFFFFFFFFFFF))
bindings = RoleBinding.objects.filter(user_id=correct_uuid, tenant_id=u.tenant_id, is_active=True)
print(f'User: {u.username}, pk={u.pk}')
print(f'Encoded UUID: {correct_uuid}')
print(f'Active bindings: {bindings.count()}')
for b in bindings:
    print(f'  Role: {b.role.name} ({b.role.permissions.count()} permissions)')
"
```

### 2. Create RoleBinding (if missing)

```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py shell -c "
import uuid
from apps.common.models.rbac import RoleBinding, Role
from apps.users.models import User

u = User.objects.get(username='$1')
admin_role = Role.objects.get(name='$2')

# CRITICAL: use encoded UUID, NOT u.pk
correct_uuid = uuid.UUID(int=0x00000000000040008000000000000000 | (u.pk & 0xFFFFFFFFFFFF))

binding, created = RoleBinding.objects.get_or_create(
    user_id=correct_uuid,
    tenant_id=u.tenant_id,
    role=admin_role,
    defaults={'is_active': True},
)
print(f'RoleBinding: {\"created\" if created else \"already exists\"}')
print(f'Role: {admin_role.name} ({admin_role.permissions.count()} permissions)')
"
```

### 3. Add missing permissions to role

```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py shell -c "
from apps.common.models.rbac import Role, Permission, RolePermission

role = Role.objects.get(name='$2')
for resource, action in [('tasks', 'read'), ('tasks', 'write'), ('tasks', 'delete')]:
    perm, _ = Permission.objects.get_or_create(resource=resource, action=action)
    RolePermission.objects.get_or_create(role=role, permission=perm)
    print(f'Added {resource}.{action}')

print(f'Role {role.name} now has {role.permissions.count()} permissions')
"
```

### 4. Verify login returns permissions

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -H "X-Gateway-Token: complect_internal_secure_mesh_token_2026" \
  -d '{"username": "$1", "password": "$3", "device_fingerprint": "verify"}' | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Permissions: {len(d.get(\"permissions\",[]))}')"
```

## Parameters
- `$1` — username (default: `testadmin`)
- `$2` — role name (default: `admin`)
- `$3` — password (default: `Admin123!`)

## Known gotcha
`is_superuser=True` does NOT grant frontend permissions. Permissions come from RoleBinding → Role → Permission chain. Without a RoleBinding, `permissions: []` and only "Главная" tab is visible in the dashboard sidebar.
