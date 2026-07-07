---
name: scaffold-frontend-feature
description: "Scaffold a new frontend dashboard feature: API hooks, list page, detail page with tabs, sidebar link. Follows existing patterns from contragents/tasks."
---

# Scaffold Frontend Dashboard Feature

Creates a complete frontend feature following the established patterns in this project.

## Prerequisites
- Backend API endpoints exist (OpenAPI schema has the types)
- Run `make generate-sdk` to get TypeScript types in `frontend/src/shared/api/generated/schema.d.ts`

## Steps

### 1. Generate SDK (if not done)
```bash
make generate-sdk
```
Verify types exist: `grep -c "TypeName" frontend/src/shared/api/generated/schema.d.ts`

### 2. Create API hooks file
**Path**: `frontend/src/shared/api/{feature}.ts`

Pattern (from `contragents.ts`, `tasks.ts`):
```typescript
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './generated/schema'

// Type aliases
export type Item = components['schemas']['ItemOut']
export type ItemList = components['schemas']['ItemListOut']

// Query keys
export const itemKeys = {
  all: ['items'] as const,
  lists: () => [...itemKeys.all, 'list'] as const,
  list: (params: Record<string, unknown>) => [...itemKeys.lists(), params] as const,
  details: () => [...itemKeys.all, 'detail'] as const,
  detail: (id: string) => [...itemKeys.details(), id] as const,
}

// Queries
export function useItems(params?) { ... }
export function useItem(id: string) { ... }

// Mutations
export function useCreateItem() { ... }
export function useUpdateItem(id: string) { ... }
export function useDeleteItem(id: string) { ... }
```

### 3. Create list page
**Path**: `frontend/src/app/dashboard/{feature}/page.tsx`

Pattern:
- Filter controls (dropdowns for status, etc.)
- "New" button → inline create form
- Table with columns: name, status (colored badge), dates
- Cursor pagination: "Load more" button
- Links to detail page on row click

### 4. Create detail page with tabs
**Path**: `frontend/src/app/dashboard/{feature}/[id]/page.tsx`

Pattern (from `contragents/[id]/page.tsx`, `tasks/[id]/page.tsx`):
- Back link to list
- Title
- Tab navigation (overview, related data, history)
- Overview tab: description list with Row component
- Related tabs: tables with actions

### 5. Add sidebar link
**File**: `frontend/src/app/dashboard/layout.tsx`

Add to NAV_ITEMS array:
```typescript
{ href: '/dashboard/{feature}', label: '{Feature Name}', permission: '{feature}.read' },
```

### 6. Type check
```bash
make tsc 2>&1 | grep "{feature}/"
```
Fix any errors before committing.

## Key patterns
- `apiClient.GET/POST/PATCH/DELETE` from `openapi-fetch`
- `useQuery` with query keys for cache invalidation
- `useMutation` with `onSuccess` invalidating related queries
- Error handling: `apiMsg(error, fallback)` helper
- Colored status badges: `TASK_STATUS_COLORS` pattern
- Row component for description lists
- Tabs: `useState<Tab>` with TAB_LABELS record
