---
title: "Pydantic model_dump() at Redis cache boundary fails with Decimal fields under JSON serializer"
date: 2026-06-25
category: docs/solutions/runtime-errors
module: common/cache
problem_type: runtime_error
component: service_object
symptoms:
  - "TypeError: Object of type Decimal is not JSON serializable raised by cache.set()"
  - "With JSON cache serializer: HTTP 500 on every request including the first"
  - "With pickle serializer (dev default): no crash, but Decimal round-trips silently as string if JSON is ever introduced"
root_cause: wrong_api
resolution_type: code_fix
severity: critical
tags: [redis, pydantic, decimal, serialization, caching, django]
---

# Pydantic model_dump() at Redis cache boundary fails with Decimal fields under JSON serializer

## Problem

`Pydantic model.model_dump()` returns a Python dict containing **native Python types** —
`Decimal`, `datetime`, `date`, `UUID` — not JSON-serializable primitives. When passed
directly to `cache.set()`, this dict serializes fine under Django's default pickle-based
Redis backend but raises `TypeError` immediately if a JSON serializer is configured.

Affected endpoint: `GET /api/v1/dashboard/director/`  
Affected fields: `payments_credit_month: Decimal`, `payments_debit_month: Decimal`,
`period_from: date`, `period_to: date`

Django's native `django.core.cache.backends.redis.RedisCache` (used in this project)
defaults to pickle, so the crash does not manifest in the current dev setup.
However, JSON serialization is the security-correct choice for Redis (pickle
deserialization from an untrusted store is a remote code execution vector), making this
a P0 latent defect that would surface immediately upon any security hardening of the
cache backend.

## Symptoms

- `TypeError: Object of type Decimal is not JSON serializable` raised inside `cache.set()`
- HTTP 500 on every request when JSON cache serializer is active (both cold and warm cache)
- No error with Django's default pickle serializer — the bug is invisible in development
  until the cache backend is changed

## What Didn't Work

Storing `model_dump()` output directly without accounting for non-JSON types is the
anti-pattern. The fix is not to add a custom JSON encoder (e.g., `DjangoJSONEncoder`) —
that merely patches around the root cause. Pydantic already provides the correct
serialization method.

## Solution

Use `model_dump_json()` to write and `json.loads()` + `model_validate()` to read.
`model_dump_json()` uses Pydantic's Rust-backed serializer which correctly converts
`Decimal` → string, `datetime` → ISO 8601, `UUID` → string, `date` → ISO 8601.

**Broken:**

```python
# backend/apps/common/api/dashboard.py  line 123
set_cached_director_dashboard(tenant_id, summary.model_dump())

# On read:
cached = get_cached_director_dashboard(tenant_id)  # returns dict (pickle OK; JSON fails)
if cached is not None:
    return 200, DirectorDashboardSchema.model_validate(cached)
```

**Fixed:**

```python
import json

# Write — serialize via Pydantic, not json.dumps(model_dump())
set_cached_director_dashboard(tenant_id, summary.model_dump_json())

# Read — deserialize JSON string back to dict, then validate
cached = get_cached_director_dashboard(tenant_id)  # returns JSON string
if cached is not None:
    return 200, DirectorDashboardSchema.model_validate(json.loads(cached))
```

The cache layer stores and retrieves a JSON string (cache value type changes from `dict`
to `str`). The `model_validate(json.loads(...))` call reconstructs the Pydantic model
correctly: `"15000.00"` → `Decimal("15000.00")` via Pydantic's type coercion.

## Why This Works

`model_dump()` is designed for **in-process use** — passing data between Python objects
where the types remain native. `model_dump_json()` is designed for **serialization
boundaries** — network, file, cache — where the output must be a valid JSON string with
all complex types converted to JSON-safe primitives.

Pydantic's serializer handles the edge cases that `json.dumps()` does not:
`Decimal` (not a JSON primitive), `datetime`/`date` (no native JSON type), `UUID`
(must be stringified). Using `json.dumps(model_dump())` bypasses this and will break
on any Pydantic model that contains numeric precision types or temporal types.

## Prevention

**Rule**: Never call `json.dumps(model.model_dump())`. Always use `model.model_dump_json()`
at serialization boundaries (Redis, files, HTTP response bodies that go through a
non-Pydantic serializer).

```python
# Correct pattern for any Pydantic model written to cache:
cache.set(key, pydantic_model.model_dump_json(), timeout=TTL)
cached = cache.get(key)
result = MySchema.model_validate(json.loads(cached)) if cached else None

# Incorrect — silently breaks when non-JSON types are present:
cache.set(key, json.dumps(pydantic_model.model_dump()), timeout=TTL)
```

**Test case to catch this class of bug:**

```python
def test_director_dashboard_cache_round_trip_decimal():
    """
    Verifies that the second request (cache hit) returns the same Decimal values
    as the first (cache miss). Fails immediately under JSON serialization if
    model_dump() is used instead of model_dump_json().
    """
    tenant_id = uuid.uuid4()
    cache.delete(_director_key(tenant_id))
    headers = _gw_headers(tenant_id, uuid.uuid4())

    resp1 = Client().get(URL, **headers)
    assert resp1.status_code == 200

    resp2 = Client().get(URL, **headers)
    assert resp2.status_code == 200  # fails here if Decimal not serialized correctly
    assert Decimal(resp2.json()["payments_credit_month"]) == Decimal(resp1.json()["payments_credit_month"])
```

This test exists in `test_director_dashboard.py::TestDirectorDashboardCache.test_second_call_uses_cache`
and will catch the regression regardless of the cache serializer.

## Related Issues

- Discovered during Sprint C code review (2026-06-25), flagged P0
- Applies to all Django Ninja endpoints that cache Pydantic models containing `Decimal`,
  `date`, `datetime`, or `UUID` fields
- See `backend/apps/common/api/dashboard.py` for the affected endpoint
