---
title: Browser Download APIs Do Not Send Authorization Headers — Use Authenticated Fetch + Blob
date: 2026-06-20
category: docs/solutions/integration-issues
module: frontend.shared.api.reports
problem_type: integration_issue
component: authentication
symptoms:
  - Excel export download fails silently with no user-visible error
  - Backend returns 401 when download is triggered via window.open() or anchor href
  - No network error appears in the UI; the file simply does not download
root_cause: missing_permission
resolution_type: code_fix
severity: high
related_components:
  - frontend.src.shared.api.client
  - frontend.src.app.dashboard.reports
tags:
  - fetch
  - authentication
  - jwt
  - file-download
  - blob
  - next-js
  - authorization-header
---

# Browser Download APIs Do Not Send Authorization Headers — Use Authenticated Fetch + Blob

## Problem

`downloadReport()` in `frontend/src/shared/api/reports.ts` used a browser `<a>` element with a raw `href` to trigger file downloads. Browser-initiated downloads via `<a href>` or `window.open()` carry no custom headers, so the `Authorization: Bearer` JWT token was never sent. The backend returned 401 and the download silently failed.

## Symptoms

- Clicking any Excel export button on contracts, finance, or payments report pages produces no file download and no visible error
- Backend returns 401 for the download request
- No network error appears in the browser console (the 401 response is swallowed silently)
- The pre-existing `downloadPdf` function in the same file worked correctly — it used `fetch` with explicit token; `downloadReport` was the inconsistent outlier

## What Didn't Work

- Investigating backend export endpoint registration — endpoints were correctly registered and reachable. The failure was entirely on the frontend transport layer: the request was unauthenticated before it reached the backend.

## Solution

Replace the `<a href>` download pattern with an authenticated `fetch` call. Updated `frontend/src/shared/api/reports.ts`:

```typescript
// BEFORE — browser <a> link (no auth headers possible)
export function downloadReport(path: string, filename?: string): void {
  const a = document.createElement('a')
  a.href = path
  if (filename) a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

// AFTER — authenticated fetch with Bearer token
import { env } from '@/shared/config/env'
import { getAccessToken } from './client'  // non-hook function backed by Zustand auth store ref

export async function downloadReport(path: string, filename?: string): Promise<void> {
  const token = getAccessToken()
  const url = `${env.NEXT_PUBLIC_API_URL}${path}`
  const response = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }
  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  if (filename) a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(objectUrl)
}
```

Three caller pages updated to handle the now-async signature with error state (`frontend/src/app/dashboard/reports/{contracts,finance,payments}/page.tsx`):

```typescript
const [exportError, setExportError] = useState('')

async function handleExport() {
  setExportError('')
  try {
    await downloadReport('/api/v1/reports/contracts/export/', 'contracts_report.xlsx')
  } catch {
    setExportError('Ошибка экспорта. Попробуйте снова.')
  }
}
// In JSX:
{exportError && <p className="text-sm text-destructive mb-4">{exportError}</p>}
```

## Why This Works

`fetch()` allows arbitrary request headers including `Authorization: Bearer <token>`. `getAccessToken()` is a non-hook function in `client.ts` backed by a Zustand store ref — safe to call from outside React components. The full backend URL must be constructed explicitly via `env.NEXT_PUBLIC_API_URL` because Next.js has no proxy configured for `/api/v1/` calls. The response is read as a `Blob`, converted to an object URL, and programmatically clicked — the browser shows the same "Save As" dialog but the request is now authenticated. `URL.revokeObjectURL()` releases the Blob from memory.

## Prevention

- Any download function targeting a protected backend endpoint must use `fetch` with an `Authorization` header — never `<a href>` or `window.open()` with a backend URL.
- Use `downloadPdf` in `reports.ts` as the reference implementation for authenticated binary downloads.
- When adding a new export function, do not copy `<a href>` patterns from non-authenticated contexts. The in-file reference is `downloadPdf`.
- Silently failing downloads (no error, no file) → open the browser Network tab. A 401 response on a `GET` to a download endpoint is the canonical symptom of missing `Authorization` header.

## Related Issues

- `frontend/src/shared/api/reports.ts` — `downloadReport` rewritten
- `frontend/src/shared/api/client.ts` — `getAccessToken` reference
- See also: `docs/solutions/integration-issues/kong-cors-login-timeout.md` (adjacent: browser requests silently dropped due to missing allowed headers at the HTTP gateway layer)
