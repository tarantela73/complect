---
title: Dev Login Uses Username Field, Not Email
date: 2026-06-23
category: developer-experience
module: apps.users
problem_type: developer_experience
component: authentication
severity: medium
symptoms:
  - "Login page returns 'Неверный логин или пароль' even when entering correct email and password"
  - Password reset via manage.py shell doesn't help when the wrong field value is entered
root_cause: inadequate_documentation
resolution_type: documentation_update
applies_when:
  - First login attempt in a new dev environment
  - After database seed or fixture load that creates users
tags:
  - dev-credentials
  - login
  - username
  - authentication
  - django
---

# Dev Login Uses Username Field, Not Email

## Context

The ERP login form has a field labelled **«Логин»** that looks like it might accept an email address. In fact the backend authenticates by Django's `username` field — not `email`. The admin seed user has `email=admin@test.com` but `username=testadmin`, so entering the email always fails.

## Guidance

When logging in to the ERP dev environment, use the **username**, not the email:

| Field | Value |
|-------|-------|
| Логин | `testadmin` |
| Пароль | `Admin123!` |

If login still fails after using the correct username, reset the password via the Django shell:

```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py shell -c "
from apps.users.models import User
u = User.objects.get(username='testadmin')
u.set_password('Admin123!')
u.save()
print('OK')
"
```

To check what username a user has:

```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py shell -c "
from apps.users.models import User
for u in User.objects.values('email', 'username', 'is_active')[:20]:
    print(u)
"
```

## Why This Matters

`auth_service.login()` does a Django ORM lookup by the `username` field:

```python
# apps/users/services/auth_service.py
user = User.objects.get(username=username, is_active=True)
```

The `LoginRequest` schema sends a `username` string, and the login form's
`<input id="username">` maps directly to that field. Email is stored separately
and is never used for authentication.

**Exception — users created via admin panel:** `UserAdminService.create_user()` sets `username=email`, so admin-created users log in with their email address. This is correct behavior — see [admin-user-creation-temp-password-flow.md](admin-user-creation-temp-password-flow.md). Only seed/manually-created users (like `testadmin`) have `username ≠ email`.

## When to Apply

- First login in any fresh dev environment
- After wiping and re-seeding the database
- When onboarding a new developer

## Examples

**Fails** (email as login):
```
Логин: admin@test.com   ← wrong field value
Пароль: Admin123!
→ Неверный логин или пароль
```

**Works**:
```
Логин: testadmin
Пароль: Admin123!
→ Login successful
```

## Related

- `apps/users/services/auth_service.py` — `login()` function
- `apps/users/schemas/auth.py` — `LoginRequest` schema (`username: str`)
- `frontend/src/app/(auth)/login/page.tsx` — login form
