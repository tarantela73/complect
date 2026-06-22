---
title: "Next.js 15 Turbopack: dynamic() with ssr:false forbidden in Server Components"
date: 2026-06-22
category: docs/solutions/build-errors
module: frontend
problem_type: build_error
component: tooling
severity: high
symptoms:
  - "Turbopack emits 'Ecmascript file had an error' at the line with dynamic() in a Server Component"
  - HTTP 500 on all routes using the affected Server Component
  - Non-existent slugs return 500 instead of 404
root_cause: wrong_api
resolution_type: code_fix
tags:
  - nextjs
  - turbopack
  - dynamic-import
  - ssr
  - server-component
  - use-client
  - next15
  - leaflet
---

# Next.js 15 Turbopack: dynamic() with ssr:false forbidden in Server Components

## Problem

`dynamic(..., { ssr: false })` вызванный напрямую в Server Component приводит к ошибке компилятора Turbopack, блокируя рендеринг всего маршрута. Все запросы к маршруту возвращали HTTP 500.

## Symptoms

- Turbopack: `Ecmascript file had an error` на строке объявления `dynamic()` в файле Server Component
- HTTP 500 на всех URL маршрута, включая валидные и несуществующие slug-и
- `/objects/non-existent-slug` возвращал 500 вместо 404
- Ошибка появляется только при использовании Turbopack (Next.js 15 dev/build) — не в webpack-режиме

## What Didn't Work

Использование `dynamic()` с `{ ssr: false }` напрямую в Server Component:

```typescript
// page.tsx — Server Component (async function, no 'use client')
const MiniMap = dynamic(
  () => import('./components/MiniMap').then((m) => m.MiniMap),
  { ssr: false }  // Turbopack отклоняет — forbidden in Server Components
)
```

Turbopack в Next.js 15 запрещает `dynamic()` с `{ ssr: false }` в Server Components на уровне компилятора — это ошибка сборки, а не runtime-ошибка. Все рабочие использования `dynamic(..., { ssr: false })` в проекте (например, `frontend/src/app/dashboard/map/page.tsx`) содержат директиву `'use client'` в начале файла.

## Solution

Создать файл-обёртку Client Component, изолирующий `dynamic()` за клиентской границей, и импортировать его из Server Component.

**Новый файл** `frontend/src/app/(public)/objects/[slug]/components/ClientMiniMap.tsx`:

```typescript
'use client'

import dynamic from 'next/dynamic'

const MiniMapDynamic = dynamic(
  () => import('./MiniMap').then((m) => m.MiniMap),
  { ssr: false },
)

export function ClientMiniMap({ lat, lon }: { lat: number; lon: number }) {
  return <MiniMapDynamic lat={lat} lon={lon} />
}
```

**Изменения в** `frontend/src/app/(public)/objects/[slug]/page.tsx`:

До:
```typescript
import dynamic from 'next/dynamic'

const MiniMap = dynamic(
  () => import('./components/MiniMap').then((m) => m.MiniMap),
  { ssr: false },
)

// в JSX:
<MiniMap lat={obj.location.lat} lon={obj.location.lon} />
```

После:
```typescript
import { ClientMiniMap } from './components/ClientMiniMap'

// в JSX:
<ClientMiniMap lat={obj.location.lat} lon={obj.location.lon} />
```

## Why This Works

Turbopack в Next.js 15 применяет строгую проверку: `dynamic()` с `{ ssr: false }` допустим **только** в Client Components. Сервер не может пропустить SSR для компонента, который он же рендерит — это концептуальное противоречие, Turbopack отклоняет его на этапе компиляции.

`ClientMiniMap` устанавливает явную клиентскую границу (`'use client'`). Server Component `page.tsx` импортирует только эту границу — сам `dynamic()` не вызывает. Цепочка: Server Component → Client Component (граница) → динамический импорт без SSR. Все корректно.

Библиотеки, требующие browser API (Leaflet, WebGL-компоненты, любые `window`/`document` зависимости) по определению требуют `{ ssr: false }` и поэтому **всегда** должны находиться за `'use client'` границей.

## Prevention

- **Code review**: при добавлении `dynamic(..., { ssr: false })` — убедиться, что файл содержит `'use client'` в первой строке. Без директивы — запрос изменений.
- **Naming convention**: компоненты-обёртки для client-only библиотек называть с префиксом `Client` (`ClientMiniMap`, `ClientChart`, `ClientMap`) — явный сигнал о клиентской границе.
- **Architectural rule**: компоненты, использующие browser-only библиотеки, всегда оборачивать в отдельный `Client*`-файл с `'use client'`, даже если кажется проще добавить `dynamic()` на месте.
- **Diagnostics**: ошибка Turbopack `Ecmascript file had an error` на строке с `dynamic` → первый шаг: проверить наличие `'use client'` в файле.

## Related Issues

- [authenticated-fetch-for-file-downloads.md](../integration-issues/authenticated-fetch-for-file-downloads.md) — другой frontend integration pattern (слабо связан)
