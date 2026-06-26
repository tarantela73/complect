---
title: "ReadYourOwnWrites Middleware: ContextVar Not Updated via Instance Attribute Assignment"
date: "2026-06-26"
category: logic-errors
module: apps/common/middleware/lifecycle
problem_type: logic_error
component: service_object
symptoms:
  - "Reads immediately after mutations can hit the replica instead of primary DB"
  - "force_primary ContextVar stays False despite the middleware running the set code path"
  - "Hardcoded Redis(host='redis', port=6379, db=1) in middleware init — breaks if Redis URL changes"
root_cause: wrong_api
resolution_type: code_fix
severity: high
related_components:
  - database
tags:
  - contextvar
  - read-your-own-writes
  - cqrs
  - middleware
  - python-descriptor
  - read-replica
  - django
---

# ReadYourOwnWrites Middleware: ContextVar Not Updated via Instance Attribute Assignment

## Problem

`ReadYourOwnWritesStickyMiddleware` (rule 4.13) appeared functional but never actually forced primary DB
routing. Writes after mutations still hit the streaming replica. Root cause: the middleware assigned
`self.context_core.force_primary = True` instead of calling `set_force_primary_db(True)`.

Additionally, the Redis client was hardcoded (`host="redis", port=6379, db=1`) instead of using
`settings.REDIS_CACHE_URL`, creating a maintenance hazard if Redis config changes.

## Symptoms

- Reads immediately following a POST/PUT/PATCH/DELETE may return stale data from the replica
- `force_primary` ContextVar never becomes `True` even though the middleware's set-branch executes
- No error raised — the assignment silently creates an instance attribute, appearing to succeed

## What Didn't Work

- **Checking Redis keys** — the Redis sticky key was being written correctly after mutations; the
  bug was in the read path, not the Redis write.
- **Checking middleware order** — middleware was ordered correctly in `MIDDLEWARE`; the middleware
  itself was executing but the ContextVar was never set.

## Solution

**Before (broken):**

```python
class ReadYourOwnWritesStickyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.context_core = ContextCore()                   # creates a fresh instance
        self.redis_client = redis.Redis(host="redis", port=6379, db=1)  # hardcoded

    def __call__(self, request):
        ...
        if self.redis_client.exists(redis_key):
            self.context_core.force_primary = True          # BUG: instance attr, not ContextVar.set()
```

**After (fixed):**

```python
from apps.common.context.core import get_tenant_id, set_force_primary_db

class ReadYourOwnWritesStickyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        # No context_core instance; use module-level helpers that call ContextVar.set()
        self.redis_client = redis.from_url(settings.REDIS_CACHE_URL, decode_responses=True)

    def __call__(self, request):
        ...
        if self.redis_client.exists(redis_key):
            set_force_primary_db(True)                      # CORRECT: calls ContextVar.set()
```

## Why This Works

`ContextCore` declares `force_primary` as a **class-level** `ContextVar`:

```python
class ContextCore:
    force_primary: ContextVar[bool | None] = ContextVar("force_primary", default=False)
```

`ContextVar.set()` is the API that updates the per-coroutine/per-thread slot. Python attribute
assignment (`self.context_core.force_primary = True`) bypasses this entirely — it creates a plain
instance attribute on the `context_core` object, shadowing the class-level descriptor. The
`is_primary_db_enforced()` function reads `ContextCore.force_primary.get()` (class-level), which
was never updated. The bug is invisible: no exception, no warning, just the ContextVar silently
ignored.

`set_force_primary_db(value)` (`apps/common/context/core.py`) is the correct call — it calls
`ContextCore.force_primary.set(value)` directly.

## Prevention

- **Never assign to a ContextVar through an instance** — always use the module-level helper
  (`set_force_primary_db`, etc.) or `ContextVar.set()` directly.
- When writing middleware that touches ContextVars, import and use the named helper functions
  (`set_force_primary_db`, `get_tenant_id`, …) rather than holding a `ContextCore()` instance.
- **Redis URLs in middleware** must come from `settings.REDIS_CACHE_URL` (or equivalent setting),
  never hardcoded. Hardcoded host/port/db breaks when Docker service names or Redis DB index change.

```python
# Correct pattern for any future middleware touching ContextVars:
from apps.common.context.core import set_force_primary_db, is_primary_db_enforced

class SomeMutatingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.redis_client = redis.from_url(settings.REDIS_CACHE_URL, decode_responses=True)

    def __call__(self, request):
        if some_condition:
            set_force_primary_db(True)   # right
            # NOT: self.ctx.force_primary = True  # wrong
```

## Related Issues

- `docs/solutions/test-failures/transaction-tests-read-stale-from-streaming-replica.md` —
  complementary: Celery workers bypass this middleware entirely and need explicit `set_force_primary_db(True)`
  inside `consumer_base.handle()`. The present bug is in the middleware itself, not in workers
  bypassing it.
- `apps/common/context/core.py` — `ContextCore`, `set_force_primary_db`, `is_primary_db_enforced`
- `apps/common/middleware/lifecycle/read_your_own_writes.py` — the fixed middleware
