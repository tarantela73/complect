---
title: RBAC Enforcement Mode LOG_ONLY silently bypasses all authorization
date: 2026-06-24
category: docs/solutions/security-issues/
module: apps.common
problem_type: security_issue
component: authentication
severity: critical
symptoms:
  - Every RBAC-guarded endpoint returned 200 OK for any authenticated user regardless of role
  - require_permission decorator did not raise PermissionDenied — silently passed all authenticated requests
  - PolicyEngine.check() in LOG_ONLY mode returns False but does not raise an exception
  - No prod.py existed — base.py LOG_ONLY setting was active in all environments
root_cause: config_error
resolution_type: config_change
related_components:
  - backend/apps/common/api/permissions.py
  - backend/config/settings/base.py
  - backend/config/settings/local.py
  - backend/config/settings/prod.py
tags:
  - rbac
  - security
  - authorization
  - config
  - enforcement-mode
  - silent-bypass
  - secure-defaults
  - django-settings
---

# RBAC Enforcement Mode LOG_ONLY silently bypasses all authorization

## Problem

`RBAC_ENFORCEMENT_MODE = "LOG_ONLY"` was set in `base.py` with no `prod.py` override. `PolicyEngine.check()` in LOG_ONLY mode logs denials and returns `False` but never raises — `require_permission` only raises on unauthenticated requests, so authorization was silently bypassed for every authenticated user in all environments.

## Symptoms

- Every RBAC-guarded endpoint returned `200 OK` for any authenticated user regardless of assigned roles
- Permission denial events appeared in structlog JSON (`"denied": true`) but produced no HTTP 403
- Tests passed — the test suite ran under `LOG_ONLY` and never tested that 403s were actually returned for unauthorized roles
- `prod.py` did not exist — `base.py` was the effective production settings file

## What Didn't Work

- **Hypothesis: LOG_ONLY is intentional dev-only and production has an override.** Disproved — `prod.py` did not exist at all. `base.py` was the sole settings source for production.
- **Checking `require_permission` for bugs.** The decorator is structurally correct; the flaw is that it delegates blocking entirely to `PolicyEngine.check()` raising an exception, which LOG_ONLY mode never does. The decorator's own docstring confirmed: "In LOG_ONLY mode the check proceeds and denial is logged only."

## Solution

**`backend/config/settings/base.py`** — secure by default:

```python
# Before
RBAC_ENFORCEMENT_MODE = "LOG_ONLY"

# After
# OFF       — bypass all checks (use in tests that don't need RBAC)
# LOG_ONLY  — evaluate and log denials, never block (local.py overrides for gradual rollout)
# ENFORCE   — raise PermissionDenied on denial (production default)
RBAC_ENFORCEMENT_MODE = "ENFORCE"
```

**`backend/config/settings/local.py`** — explicit dev override:

```python
# Gradual rollout: log denials without blocking during development.
RBAC_ENFORCEMENT_MODE = "LOG_ONLY"
```

**`backend/config/settings/prod.py`** (created — did not exist before):

```python
import os
from .base import *  # noqa: F401, F403

DEBUG = False
ENVIRONMENT = "production"
RBAC_ENFORCEMENT_MODE = "ENFORCE"

SECRET_KEY = os.environ["SECRET_KEY"]
ALLOWED_HOSTS = os.environ["ALLOWED_HOSTS"].split(",")
EXPECTED_GATEWAY_TOKEN = os.environ["EXPECTED_GATEWAY_TOKEN"]  # no default = crash on missing
# All secrets via os.environ["KEY"] — no defaults anywhere in prod.py
```

## Why This Works

`require_permission` raises `PermissionDenied` only if `PolicyEngine.check()` raises. ENFORCE mode is the only mode where `PolicyEngine.check()` raises on denial. Setting `ENFORCE` as the `base.py` default means any settings file that omits an override inherits blocking behavior — the secure path. `local.py` explicitly opts back into LOG_ONLY for gradual RBAC rollout during development.

## Prevention

1. **Default to the strictest mode in base.py.** Security-critical settings (`RBAC_ENFORCEMENT_MODE`, `DEBUG`) must default to production-safe values. Development relaxes them explicitly in `local.py`. Never default to a permissive mode and rely on environments to override.

2. **All prod secrets must have no default.** Use `os.environ["KEY"]` (not `os.getenv("KEY", "fallback")`) in `prod.py`. Missing a required secret crashes at startup rather than silently using a dev fallback in production.

3. **Test RBAC permission denial with `ENFORCE` explicitly active:**

```python
@pytest.fixture(autouse=True)
def _rbac_enforce(self, settings):
    settings.RBAC_ENFORCEMENT_MODE = "ENFORCE"

def test_no_permission_returns_403(self):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    resp = Client().get(BASE_URL, **_headers(tenant_id, user_id))
    assert resp.status_code == 403
```

Tests that use `RBAC_ENFORCEMENT_MODE = "OFF"` to bypass RBAC must never assert `403` — and tests that assert `403` must explicitly set `ENFORCE`. Never rely on the default from settings — `local.py` uses LOG_ONLY, which makes 403 assertions impossible to trigger without the override.

4. **Security review checklist:** any new `require_permission` usage or `PolicyEngine` mode change must include a test asserting `403` with ENFORCE active and `200` with a correctly-granted role.

## Related Issues

- [tenant-isolation-in-services.md](../patterns/tenant-isolation-in-services.md) — separate security layer: tenant isolation at ORM level via ADR-0021 Anti-IDOR Guard
