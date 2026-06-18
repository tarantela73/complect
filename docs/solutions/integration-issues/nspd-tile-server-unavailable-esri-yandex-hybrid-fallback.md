---
title: НСПД тайловый сервер недоступен публично — переход на ESRI World Imagery и Yandex Hybrid
date: 2026-06-17
category: integration-issues
module: gis
problem_type: integration_issue
component: tooling
symptoms:
  - Слой «Кадастр» на карте пуст — тайлы не загружаются, видимых ошибок в UI нет
  - DevTools Network показывает HTTP 200 на тайловые запросы с Content-Type text/html вместо image/png
  - curl на тайловый URL возвращает HTML SPA вместо PNG — <!DOCTYPE html> (НСПД веб-приложение)
  - Слой ЗОУИТ также пуст — silent failure, нет tileerror обработчика в MapView
  - Яндекс спутниковый слой показывает объекты со смещением 50–200 м относительно OSM
root_cause: wrong_api
resolution_type: config_change
severity: high
related_components:
  - frontend_stimulus
tags:
  - nspd
  - cadastral-tiles
  - yandex-offset
  - wgs84
  - esri-world-imagery
  - yandex-hybrid
  - tile-providers
  - gis
---

# НСПД тайловый сервер недоступен публично — переход на ESRI World Imagery и Yandex Hybrid

## Problem

Кадастровая подложка и слой ЗОУИТ перестали отображаться на карте после ликвидации `pkk.rosreestr.ru` (301 → `nspd.gov.ru`). При этом спутниковый слой Яндекса показывал GIS-объекты со смещением 50–200 м. Пользователи видели пустые слои без индикации ошибки.

## Symptoms

- Слой «Кадастр» на карте пуст — тайлы не загружаются, видимых ошибок в UI нет
- DevTools Network показывает HTTP 200 на тайловые запросы с `Content-Type: text/html` вместо `image/png`
- `curl -s nspd.gov.ru/arcgis/rest/services/PKK6/.../tile/15/10427/5011 | head -c 200` → `<!DOCTYPE html>` (HTML SPA)
- `file /tmp/tile.bin` → "HTML document, Unicode text, UTF-8 text"
- Слой ЗОУИТ («Коммуникации») также пуст без ошибок в консоли — в `MapView` не было `tileerror` обработчика для этого слоя
- Яндекс спутниковый слой показывает систематическое смещение объектов ~50–200 м относительно OSM и ESRI

## What Didn't Work

- **Checking only HTTP status and body size**: `curl -w "%{http_code}|%{size_download}"` вернул `200|15116` — принято за успех (PNG ~15 KB), но тело было HTML SPA. Без проверки `Content-Type` ошибка не выявлена.
- **Referer header**: `Referer: https://nspd.gov.ru/` не изменил ответ — НСПД возвращает SPA для всех путей.
- **`?f=image` query param**: ArcGIS-параметр форсирования картинки не работает — НСПД не является публичным ArcGIS MapServer.
- **Альтернативные домены Росреестра**: `gis.rosreestr.ru`, `geo.rosreestr.ru` → TCP timeout (недоступны из внешней сети).
- **WMS-подход** (из ранней версии): `WMSTileLayer` с `CadastreSelected/WMSServer` — это endpoint для выбранных объектов, не для кадастровой сетки. Leaflet отображал пустой слой. (session history)
- **OpenInfraMap** (`openinframap.org/tiles/{z}/{x}/{y}.png`): сервис отдаёт **vector tiles (MVT)**, не PNG raster — Leaflet `TileLayer` молча рендерил ничего. (session history)
- **`LayersControl.Overlay checked={showCadastral}`**: проп `checked` у Leaflet overlay не реагирует на программные изменения состояния React — toggle не работал. Заменено на условный рендеринг `{showCadastral && <TileLayer />}`. (session history)

## Solution

### Диагностика Content-Type

```bash
# Правильная проверка — всегда верифицировать Content-Type
curl -sk -o /dev/null -w "HTTP %{http_code}, CT: %{content_type}" \
  "https://nspd.gov.ru/arcgis/rest/services/PKK6/CadastreObjects/MapServer/tile/15/10427/5011"
# → HTTP 200, CT: text/html  ← не image/png!

# Для ESRI и Yandex — image/jpeg подтверждено:
curl -sk -o /dev/null -w "%{content_type}" \
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/13/2714/5014"
# → image/jpeg ✓

curl -sk -o /dev/null -w "%{content_type}" \
  "https://core-sat.maps.yandex.net/tiles?l=sat,skl&x=5014&y=2714&z=13&scale=1"
# → image/jpeg ✓
```

### `frontend/src/shared/ui/map/tile-providers.ts`

```typescript
// Флаги доступности внешних сервисов — единственная точка изменения
// 2026-06-17: nspd.gov.ru returns SPA HTML for all /arcgis/.../tile/* paths.
// Flip MAP_CADASTRAL_AVAILABLE to true when a working tile endpoint is confirmed.
export const MAP_CADASTRAL_AVAILABLE = false

// Fallback to OpenRailwayMap (railway corridors) until НСПД ЗОУИТ is restored.
export const MAP_ZOUIT_ENABLED = false

// Добавить Яндекс Гибрид (sat+skl в одном запросе)
const YANDEX_HYBRID: TileProvider = {
  id: 'yandex-hybrid',
  label: 'Яндекс (гибрид)',
  url: 'https://core-sat.maps.yandex.net/tiles?l=sat,skl&x={x}&y={y}&z={z}&scale=1&lang=ru_RU',
  attribution: '&copy; <a href="https://yandex.ru/maps">Яндекс.Карты</a>',
  maxZoom: 19,
}

// WGS84-точный спутник — совпадает с OSM/PostGIS координатами, без CRS-сдвига
const ESRI: TileProvider = {
  id: 'esri',
  label: 'Спутник (ESRI)',
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  attribution: '&copy; <a href="https://www.esri.com">Esri</a>, DigitalGlobe, GeoEye, USGS',
  maxZoom: 18,
}

export const ALL_PROVIDERS: TileProvider[] = [OSM, YANDEX, YANDEX_HYBRID, GIS2, ESRI]
```

### `frontend/src/app/dashboard/map/page.tsx`

```tsx
import { ALL_PROVIDERS, MAP_CADASTRAL_AVAILABLE } from '@/shared/ui/map/tile-providers'

<label
  className={`flex items-center gap-1 text-xs ${
    MAP_CADASTRAL_AVAILABLE ? 'cursor-pointer' : 'cursor-not-allowed opacity-50'
  }`}
  title={
    !MAP_CADASTRAL_AVAILABLE
      ? 'Кадастровые тайлы временно недоступны — НСПД Росреестра не предоставляет публичный тайловый сервис'
      : 'Кадастровые границы'
  }
>
  <input type="checkbox" disabled={!MAP_CADASTRAL_AVAILABLE} ... />
```

## Why This Works

**НСПД (кадастр и ЗОУИТ):**
`pkk.rosreestr.ru/arcgis/` был ликвидирован и сделал 301-редирект на `nspd.gov.ru`. Новый домен — это catch-all SPA (React): каждый URL, включая `/arcgis/rest/services/.../tile/{z}/{y}/{x}`, возвращает один и тот же HTML файл веб-приложения (~15 KB). Публичного ArcGIS MapServer тайлового API для кадастровых данных РФ не существует ни на nspd.gov.ru, ни на альтернативных доменах Росреестра. SSL-сертификат НСПД самоподписанный — браузеры блокируют запросы на сетевом уровне. Флаги `MAP_CADASTRAL_AVAILABLE = false` и `MAP_ZOUIT_ENABLED = false` отключают слои проактивно вместо молчаливого падения.

**Яндекс спутниковый CRS-сдвиг:**
Яндекс применяет криптографический сдвиг координат согласно **ФЗ-172** («О геодезии, геодезической и картографической деятельности»). Спутниковые тайлы выровнены по проприетарной СК Яндекса, а не WGS84. GIS-объекты в PostGIS хранятся в WGS84 → систематическое смещение 50–200 м в определённом направлении. Это не баг инверсии оси Y и не проблема URL — сдвиг зашит в тайловые изображения. ESRI World Imagery использует WGS84/WebMercator без поправок, поэтому совпадает с OSM и точками объектов.

**Яндекс Hybrid (`l=sat,skl`):**
Параметр `l=sat,skl` запрашивает спутниковые тайлы с наложенными подписями улиц в одном HTTP-запросе. Ответ `image/jpeg` подтверждён (9.6 KB против 7.2 KB для чистого `l=sat`). Не требует стекирования двух `TileLayer` — один запрос, один провайдер.

## Prevention

- **Всегда проверять `Content-Type` при диагностике тайловых endpoint**: `curl -w "%{content_type}"`. HTTP 200 с `text/html` означает SPA или редирект-страницу, не изображение.
- **Добавлять `tileerror` обработчик для каждого overlay `TileLayer`**: без него браузерные ошибки загрузки тайлов остаются silent failure. Пример в `MapView.tsx` для PKK-слоя.
- **Выносить URL и флаги доступности внешних сервисов в константы** (`MAP_CADASTRAL_AVAILABLE`, `MAP_ZOUIT_ENABLED`): переключение при восстановлении сервиса — одна строка в одном файле без деплоя кода.
- **Документировать CRS-совместимость при добавлении спутниковых провайдеров**: Яндекс и 2ГИС применяют ФЗ-172 сдвиг → не совпадают с WGS84. OSM, ESRI, Google Satellite работают в WGS84 → совпадают с PostGIS координатами объектов.

## Related Issues

- `docs/plans/2026-06-17-001-fix-pkk-nspd-migration-plan.md` — план миграции PKK→НСПД. KTD-1 ("обновить URL на nspd.gov.ru, сохранить путь PKK6") оказался недостаточным — НСПД не является ArcGIS сервером. Статус плана: superseded.
- `docs/brainstorms/2026-06-17-gis-cadastral-object-binding-requirements.md` — требования к привязке кадастрового участка к `SupplyObject`. Примечание: `pkk_client.py` работает в режиме graceful degradation — `pkk.rosreestr.ru/api/features/1` перенаправляет на НСПД SPA, поиск возвращает `None`.
- `docs/plans/2026-06-16-001-feat-map-search-layers-plan.md` — ввёл константу `MAP_ZOUIT_ENABLED` для ЗОУИТ/OpenRailwayMap fallback. Флаг остаётся актуальным, сейчас установлен в `false`.
