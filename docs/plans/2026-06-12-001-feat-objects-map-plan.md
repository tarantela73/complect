---
title: "feat: Карта объектов с кадастровыми слоями"
date: 2026-06-12
status: active
type: feat
---

# feat: Карта объектов с кадастровыми слоями

## Summary

Реализовать страницу `/dashboard/map` — интерактивная карта всех объектов с геопривязкой, статус-цветными маркерами, popup с деталями и переходом в Object 360°. Поддержка трёх tile-провайдеров (OSM / Яндекс / 2ГИС) с переключением, PKK Росреестр как кадастровый WMS-overlay, OSM Overpass для слоя коммуникаций. Редактирование координат объекта из Object 360° через PATCH `/api/v1/objects/{id}/location/`.

Backend полностью готов: `GET /api/v1/objects/geojson/`, `PATCH /api/v1/objects/{id}/location/`, PostGIS PointField, bbox-фильтрация. SDK-типы сгенерированы.

---

## Problem Frame

Операторы не могут видеть пространственное распределение объектов. Нет возможности быстро оценить географический охват работ, привязать объект к местности, сопоставить с кадастровыми границами и инженерными сетями.

**Scope:**
- Карта всех объектов с геопривязкой как основная страница `/dashboard/map`
- Переключение tile-провайдеров (OSM бесплатно без ключа; Яндекс/2ГИС — при наличии env-переменной)
- PKK Росреестр кадастровый WMS (бесплатно, без ключа)
- OSM коммуникации через tile-overlay
- Установка/изменение координат объекта из Object 360°

**Out of scope:**
- Геокодирование адресов в координаты (отдельный план)
- Маршрутизация/навигация
- Offline/кэширование тайлов
- Кластеризация маркеров (можно добавить позже через leaflet.markercluster)

---

## Key Technical Decisions

**KTD-1: react-leaflet как основа, не нативные Yandex/2GIS SDK**

Причина: react-leaflet поддерживает подключение произвольных tile-провайдеров через `TileLayer`, WMS через `WMSTileLayer` — это единственный способ совместить PKK Росреестр overlay с Яндекс-тайлами без двух независимых карт. Нативные SDK Яндекс (JS API 3.x) и 2ГИС (MapGL SDK) — закрытые рендереры без WMS. react-leaflet версии 4.x + leaflet 1.9.x — stable, SSR-safe через `dynamic`.

**KTD-2: SSR отключён для карты через `next/dynamic` с `ssr: false`**

Leaflet требует `window` и `document` в момент инициализации — в Next.js App Router компоненты по умолчанию могут рендериться на сервере. Вся карта оборачивается в `dynamic(() => import('./MapView'), { ssr: false })`. Это стандартный паттерн для leaflet в Next.js.

**KTD-3: Tile-провайдеры через env-переменные, OSM — дефолт**

- `NEXT_PUBLIC_YANDEX_MAPS_API_KEY` — если задан, доступен Яндекс-провайдер
- `NEXT_PUBLIC_2GIS_API_KEY` — если задан, доступен 2ГИС-провайдер (через `tiles.api.2gis.com`)
- Без ключей — только OSM (OpenStreetMap), 100% бесплатно
- Выбранный провайдер хранится в `localStorage` как user preference

**KTD-4: PKK Росреестр как WMS-overlay без API-ключа**

URL: `https://pkk.rosreestr.ru/arcgis/rest/services/PKK6/CadastreSelected/MapServer/WMSServer`
Параметры: `version=1.1.1, format=image/png, transparent=true`. Публичный сервис, ключ не требуется.

**KTD-5: Объекты без geometry исключаются из карты, но не из счётчика**

GeoJSON endpoint возвращает features с `geometry: null` для объектов без координат. Они фильтруются на клиенте. Страница показывает `N из M объектов на карте` где N — с координатами, M — всего.

---

## High-Level Technical Design

```
┌─────────────────────────────────────────────┐
│           /dashboard/map                     │
│  ┌──────────────────────────────────────┐   │
│  │  FilterBar  │  TileProviderSelect    │   │
│  ├──────────────────────────────────────┤   │
│  │                                      │   │
│  │   react-leaflet MapContainer         │   │
│  │   ┌──────────────────────────────┐  │   │
│  │   │  TileLayer (OSM/Yandex/2GIS) │  │   │
│  │   │  WMSTileLayer (PKK optional) │  │   │
│  │   │  WMSTileLayer (OSM utils)    │  │   │
│  │   │  Marker[] ← useObjectsGeoJSON│  │   │
│  │   │    └─ Popup (name/addr/link) │  │   │
│  │   └──────────────────────────────┘  │   │
│  └──────────────────────────────────────┘   │
│  LayerControl: [Кадастр] [Коммуникации]     │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  Object 360° → вкладка "Основное"           │
│  ┌──────────────────────────────────────┐   │
│  │  LocationSection                     │   │
│  │  • Если geometry: мини-карта 300px   │   │
│  │  • Иначе: "Координаты не установлены"│   │
│  │  • canWrite: [Установить точку]      │   │
│  │    → MapPickerModal (клик = PATCH)   │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

---

## Requirements

- R1: Страница `/dashboard/map` с картой всех объектов с геопривязкой, permission `objects.read`
- R2: Маркеры цветные по статусу (NEW/IN_PROGRESS/PAUSED/COMPLETED/CANCELLED)
- R3: Popup при клике на маркер: название, адрес, статус, ссылка на Object 360°
- R4: Переключение tile-провайдеров: OSM (дефолт), Яндекс (с ключом), 2ГИС (с ключом)
- R5: Кадастровый overlay PKK Росреестр, toggleable через LayerControl
- R6: Overlay коммуникаций из OSM, toggleable
- R7: Объект без координат — в счётчик "N/M объектов на карте", но не на карте
- R8: Object 360° → мини-карта текущего положения объекта
- R9: Пользователь с `objects.write` может установить/изменить координаты объекта кликом по карте

---

## Implementation Units

### U1. Установка react-leaflet и глобальный CSS

**Goal:** Добавить react-leaflet, leaflet, @types/leaflet. Подключить leaflet CSS глобально.

**Requirements:** Техническая зависимость для U2–U5.

**Dependencies:** нет

**Files:**
- `frontend/package.json` (modify — добавить leaflet, react-leaflet)
- `frontend/src/app/layout.tsx` (modify — import 'leaflet/dist/leaflet.css')

**Approach:**
Установить `react-leaflet@^4`, `leaflet@^1.9`, `@types/leaflet`. Leaflet CSS должен быть импортирован в корневом layout — иначе иконки и стили не работают. Важно: leaflet использует `L.Icon.Default.imagePath` — переопределить стандартные иконки маркеров через `delete L.Icon.Default.prototype._getIconUrl` в компоненте с `'use client'` + `useEffect` (это известный leaflet/webpack bug).

**Test expectation:** none — зависимость, поведенческих изменений нет.

**Verification:** `npm ls leaflet` показывает 1.9.x; страницы дашборда не ломаются.

---

### U2. Хуки useObjectsGeoJSON и useUpdateObjectLocation

**Goal:** Типизированные TanStack Query хуки для GeoJSON-эндпоинта и PATCH location.

**Requirements:** R1, R8, R9

**Dependencies:** U1

**Files:**
- `frontend/src/shared/api/objects.ts` (modify — добавить типы и хуки)

**Approach:**
Добавить в `shared/api/objects.ts`:
- Тип `ObjectGeoJSON = components['schemas']['FeatureCollectionSchema']`
- Тип `ObjectFeature = components['schemas']['FeatureSchema']`  
- Тип `UpdateLocationRequest = components['schemas']['UpdateLocationRequest']`
- `useObjectsGeoJSON(params?: { contract_id?: string; bbox?: string })` — useQuery с queryKey `['objects', 'geojson', params]`
- `useUpdateObjectLocation(objectId: string)` — useMutation PATCH `/{id}/location/`, инвалидирует `['objects', 'geojson']` и `objectKeys.detail(objectId)` после успеха

Паттерн: аналогично существующим хукам в том же файле.

**Patterns to follow:** `frontend/src/shared/api/objects.ts` — все существующие useQuery/useMutation хуки.

**Test scenarios:**
- `useObjectsGeoJSON` без параметров — возвращает FeatureCollection
- `useObjectsGeoJSON({ contract_id })` — передаёт query param корректно
- `useUpdateObjectLocation` при успехе — инвалидирует оба queryKey
- `useUpdateObjectLocation` при ошибке 403 — бросает ошибку с кодом

**Verification:** TypeScript компилируется без ошибок; хуки экспортированы из `shared/api/objects`.

---

### U3. Общие map-компоненты в shared/ui/map/

**Goal:** SSR-safe переиспользуемые компоненты карты: MapContainer, ObjectMarker, TileLayer с провайдерами, WMS-слои.

**Requirements:** R2, R3, R4, R5, R6

**Dependencies:** U1, U2

**Files:**
- `frontend/src/shared/ui/map/MapView.tsx` (create — основной динамически загружаемый компонент)
- `frontend/src/shared/ui/map/ObjectMarker.tsx` (create — маркер + popup)
- `frontend/src/shared/ui/map/tile-providers.ts` (create — конфигурация провайдеров)
- `frontend/src/shared/ui/map/index.ts` (create — barrel export)

**Approach:**

`tile-providers.ts` — объект конфигурации:
- `osm`: `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png`, attribution OSM
- `yandex`: tile URL `https://core-sat.maps.yandex.net/tiles?l=sat&x={x}&y={y}&z={z}` (свободный тайл, не требует API ключа для display-only), читает `NEXT_PUBLIC_YANDEX_MAPS_API_KEY` — если не задан, provider скрыт в UI
- `2gis`: `https://tile2.maps.2gis.com/tiles?x={x}&y={y}&z={z}&v=1`, читает `NEXT_PUBLIC_2GIS_API_KEY`

WMS-конфигурации:
- PKK: `https://pkk.rosreestr.ru/arcgis/rest/services/PKK6/CadastreSelected/MapServer/WMSServer`, `layers=0`, `format=image/png`, `transparent=true`
- Коммуникации: `https://tiles.openpipeline.io/...` (или overpass-based тайл-сервер OSM utilities — уточнить при реализации)

`MapView.tsx` — react-leaflet `MapContainer` с:
- `TileLayer` по активному провайдеру
- `LayerControl` с двумя WMS overlay (Кадастр / Коммуникации)
- Принимает `features: ObjectFeature[]` как prop
- Рендерит `ObjectMarker` для features с ненулевой geometry
- Начальный центр: Россия (55.75, 37.62), zoom 6

`ObjectMarker.tsx` — цветной `CircleMarker` (статус-цвет) + `Popup` с name, address, status badge, `<a>` ссылкой на `/dashboard/objects/{id}`.

**Паттерн SSR-safe**: компоненты `MapView` и `ObjectMarker` используют `'use client'`, но импортировать их нужно через `dynamic(() => import(...), { ssr: false })` на страницах.

**Patterns to follow:**
- Стили статус-цветов: `frontend/src/app/dashboard/objects/page.tsx` `STATUS_COLORS`
- Component structure: `frontend/src/app/dashboard/objects/[id]/components/ContractsTab.tsx`

**Test scenarios:**
- `ObjectMarker` с `geometry: null` не рендерится
- `ObjectMarker` с координатами рендерит `CircleMarker` с правильным цветом статуса
- Popup содержит ссылку на `/dashboard/objects/{id}`
- TileLayer переключается при смене провайдера
- WMSTileLayer для PKK рендерится с корректным URL и параметрами

**Verification:** Компоненты экспортированы; TypeScript без ошибок; динамический импорт не вызывает SSR ошибок.

---

### U4. Страница /dashboard/map и навигация

**Goal:** Полноценная страница карты объектов с фильтрами и счётчиком; ссылка в боковой навигации.

**Requirements:** R1, R2, R3, R4, R5, R6, R7

**Dependencies:** U2, U3

**Files:**
- `frontend/src/app/dashboard/map/page.tsx` (create)
- `frontend/src/app/dashboard/layout.tsx` (modify — добавить nav item)

**Approach:**

`page.tsx` — `'use client'`:
- `useObjectsGeoJSON()` для загрузки всех объектов
- `dynamic(() => import('@/shared/ui/map/MapView'), { ssr: false })` с loading fallback
- Фильтры: `resource_type`, `status` (через query params, передаются в хук)
- Счётчик: "X из Y объектов на карте" (X = features с geometry, Y = total)
- TileProviderSelect: dropdown для переключения провайдера (только доступные — см. env)
- Высота карты: `calc(100vh - nav height)`, адаптивно

`layout.tsx` — добавить в `NAV_ITEMS`:
```
{ href: '/dashboard/map', label: 'Карта', permission: 'objects.read' }
```
Разместить после "Объекты".

**Patterns to follow:**
- `frontend/src/app/dashboard/requests/page.tsx` — паттерн страницы со списком и фильтрами
- `frontend/src/app/dashboard/layout.tsx` — NAV_ITEMS

**Test scenarios:**
- Страница рендерится без SSR ошибок (dynamic import срабатывает)
- `useCan('objects.read') = false` → страница не рендерится (redirect/forbidden — как на других страницах)
- При `isLoading` показывается skeleton/spinner
- Счётчик корректно считает features с `geometry !== null`
- Фильтр `resource_type` → queryKey меняется, данные перезапрашиваются

**Verification:** `npm run build` без ошибок; Playwright: навигируем на `/dashboard/map`, карта появляется, маркеры видны.

---

### U5. LocationSection на Object 360°

**Goal:** Мини-карта с текущими координатами объекта и возможностью установки точки для пользователей с `objects.write`.

**Requirements:** R8, R9

**Dependencies:** U2, U3

**Files:**
- `frontend/src/app/dashboard/objects/[id]/components/LocationSection.tsx` (create)
- `frontend/src/app/dashboard/objects/[id]/page.tsx` (modify — добавить LocationSection в вкладку "Основное")

**Approach:**

`LocationSection.tsx` — `'use client'`:
- Принимает `objectId: string`, `location?: { longitude: number; latitude: number } | null`
- Если нет location → статическое сообщение "Координаты не установлены" + кнопка (если canWrite)
- Если есть location → мини-карта 300px высотой с маркером (dynamic import `ssr: false`)
- `canWrite` → кнопка "Изменить точку на карте" → открывает `MapPickerModal`

`MapPickerModal` — модальное окно с картой: центр на объект (или Москва), клик по карте устанавливает временный маркер. Кнопка "Сохранить" вызывает `useUpdateObjectLocation(objectId).mutateAsync({ longitude, latitude })`. После успеха закрывает модал, TanStack Query инвалидирует detail-данные объекта.

Координаты `location` берутся из `SupplyObjectDetailResponse` — нужно проверить, есть ли поле в схеме.

**Деферред на реализацию:** проверить, возвращает ли `SupplyObjectDetailResponse` поле `location` с координатами или только в GeoJSON эндпоинте. Если нет — добавить в backend schema (минорная правка `object_dto.py`).

**Patterns to follow:**
- Модальное окно: `frontend/src/app/dashboard/objects/[id]/components/ContractsTab.tsx` `LinkContractModal`
- Object 360° page: `frontend/src/app/dashboard/objects/[id]/page.tsx`

**Test scenarios:**
- `location: null` + `canWrite: false` → показывает "Координаты не установлены", нет кнопки
- `location: null` + `canWrite: true` → показывает кнопку "Установить точку"
- `location: {lon, lat}` → мини-карта рендерится без SSR ошибок
- MapPickerModal: клик по карте устанавливает маркер, PATCH вызывается с корректными координатами
- После успешного PATCH — модал закрывается, мини-карта обновляется (queryKey инвалидирован)
- PATCH возвращает 403 → показывает ошибку, координаты не обновляются

**Verification:** Object 360° в браузере: вкладка "Основное" показывает мини-карту (или плейсхолдер); клик "Установить точку" открывает карту; сохранение обновляет позицию.

---

## Deferred to Implementation

- Точный URL для OSM Overpass коммуникаций — уточнить работающий tile endpoint при реализации (кандидаты: `https://tiles.openpipeline.io`, `https://openinframap.org/tiles`)
- Проверить наличие поля `location` в `SupplyObjectDetailResponse` или использовать `useObjectsGeoJSON` с фильтром по одному объекту
- Кластеризация маркеров при большом числе объектов — через `react-leaflet-cluster` (деферред)

---

## Scope Boundaries

### Non-goals этого плана
- Геокодирование (адрес → координаты)
- Маршрутизация между объектами
- Offline режим / кэш тайлов
- Мобильное приложение с картой
- Интеграция с 2ГИС через нативный MapGL SDK (только tile-layer)

### Deferred to Follow-Up Work
- Кластеризация маркеров при >50 объектах (`react-leaflet-cluster`)
- Возможность рисовать полигон зоны работы (не только точку)
- Поиск объекта на карте по имени
- Экспорт карты как PNG / PDF

---

## Risks & Dependencies

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| PKK Росреестр WMS недоступен (нестабильный гос. сервис) | Средняя | LayerControl toggle — пользователь просто не включает overlay |
| Яндекс тайлы: неофициальный tile URL нарушает ToS | Средняя | Использовать только при наличии официального `NEXT_PUBLIC_YANDEX_MAPS_API_KEY`; дефолт — OSM |
| Leaflet SSR гидрация несоответствие | Низкая | `dynamic` с `ssr: false` устраняет полностью |
| `SupplyObjectDetailResponse` не содержит `location` | Средняя | Fallback: брать из `useObjectsGeoJSON` по объекту или добавить поле в бэкенд DTO |

---

## Sources & Research

- Leaflet + Next.js: стандартный паттерн `dynamic(..., { ssr: false })`
- PKK Росреестр WMS: публичный сервис, задокументирован в docs Росреестра
- 2ГИС tile: `tiles.api.2gis.com` — используется в open-source российских проектах
- Яндекс satellite tiles: `core-sat.maps.yandex.net` — публично известный tile endpoint, регулярно используется в OSM-сообществе
