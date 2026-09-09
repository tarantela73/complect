---
title: Django Ninja Routes Require Trailing Slashes to Match Requests
date: 2026-06-20
category: docs/solutions/runtime-errors
module: apps.files
problem_type: runtime_error
component: rails_controller
symptoms:
  - Tests using trailing-slash URLs receive HTML Django 404 instead of JSON response
  - URL dispatcher returns 404 before reaching any Django Ninja endpoint
  - All other routers in the project use trailing slashes but the new router does not
root_cause: wrong_api
resolution_type: code_fix
severity: high
related_components:
  - apps.files.tests
  - apps.contracts.api
  - apps.contragents.api
tags:
  - django-ninja
  - routing
  - trailing-slash
  - api
  - url-convention
---

# Django Ninja Routes Require Trailing Slashes to Match Requests

## Problem

Routes in `files_api.py` were defined without trailing slashes, while all test clients and all other API routers in the project use trailing slashes by convention. Django Ninja registers route paths verbatim, so every request with a trailing slash returned Django's HTML 404 before reaching the endpoint.

## Symptoms

- `test_delete_available_file_returns_204` fails with `assert 404 == 204`
- `test_delete_legal_hold_returns_422` fails with `assert 404 == 422`
- Response body is an HTML Django 404 page, not JSON — indicating URL routing failure, not endpoint logic failure
- `client.delete('/api/v1/files/{uuid}', ...)` (no trailing slash) returns JSON 403 (endpoint reached); `client.delete('/api/v1/files/{uuid}/', ...)` (with trailing slash) returns HTML 404 (URL never matched)

## What Didn't Work

- Investigating RBAC middleware: `ContextIsolationMiddleware`, `AsgiTenantEnforcementMiddleware`, `apply_propagation_context`, `ContextGuard.assert_clean()` — all inspected, none were the cause
- Suspecting ContextVar state leakage between tests
- Checking RBAC enforcement mode and permission seeding

The diagnostic dead-end happened because the HTML 404 body was not immediately noticed — it looked like a legitimate endpoint 404 until the response body was inspected directly.

## Solution

Added trailing slashes to all four route definitions in `backend/apps/files/api/files_api.py`:

```python
# BEFORE — routes missing trailing slash
@router.get("/{file_id}", response={200: FileDetailResponse, 403: dict})
@router.delete("/{file_id}", response={204: None, 403: dict, 422: dict})
@router.post("/{file_id}/complete", response={200: FileDetailResponse, 400: dict, 403: dict, 409: dict})
@router.get("/{file_id}/download-url", response={200: DownloadUrlResponse, 403: dict, 422: dict})

# AFTER — trailing slashes match project convention
@router.get("/{file_id}/", response={200: FileDetailResponse, 403: dict})
@router.delete("/{file_id}/", response={204: None, 403: dict, 404: dict, 422: dict})
@router.post("/{file_id}/complete/", response={200: FileDetailResponse, 400: dict, 403: dict, 409: dict})
@router.get("/{file_id}/download-url/", response={200: DownloadUrlResponse, 403: dict, 422: dict})
```

Also corrected `test_delete_legal_hold_returns_422` → `test_delete_legal_hold_returns_403`: Legal Hold is an authorisation block returning 403, not a business rule violation (422), per the API error contract in `docs/Project_architecture.md` section 6.6 and ADR-0024's typed held-command semantics.

## Why This Works

Django Ninja registers route paths verbatim. A route defined as `/{file_id}` registers exactly that path — no trailing slash. When the test client sends `DELETE /api/v1/files/{uuid}/`, Django's URL resolver finds no matching pattern and returns its default HTML 404 page — the request never reaches Django Ninja or any middleware. The JSON 404 an endpoint emits for "file not found" is only reachable after URL matching succeeds.

All other API routers in the codebase (`contragents/api.py`, `contracts/api.py`) define routes with trailing slashes, establishing the project-wide convention. `files_api.py` was the sole inconsistency.

## Prevention

- When adding a new router file, inspect an existing router (`apps/contragents/api.py` or `apps/contracts/api.py`) to confirm the trailing slash convention before writing any routes.
- When a test returns an HTML 404 (body starts with `<!DOCTYPE html>`), the first diagnostic is routing: "did the URL match any registered route?" — not middleware. Confirm by removing the trailing slash from the test URL: if the response changes from HTML to JSON, the route definition is missing a slash.
- Code review checklist: all resource-level route paths in a new Django Ninja router must end with `/`.

## Related Issues

- `backend/apps/files/api/files_api.py` — routes fixed
- `backend/apps/files/tests/test_file_list_and_delete.py` — test name and assertion corrected
- Reference convention: `backend/apps/contragents/api.py`, `backend/apps/contracts/api.py`
