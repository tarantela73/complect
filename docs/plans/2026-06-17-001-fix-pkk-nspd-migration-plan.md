---
title: "fix: Migrate cadastral map from PKK to NSPD"
date: 2026-06-17
status: active
type: fix
origin: docs/brainstorms/2026-06-17-gis-cadastral-object-binding-requirements.md
---

## Summary

ПКК (`pkk.rosreestr.ru`) ликвидирован — весь трафик редиректит на НСПД (`nspd.gov.ru`). Тайлы кадастровой сетки и ЗОУИТ работают на НСПД по тому же пути PKK6. API `features/1` для поиска полигонов недоступен без авторизации Госуслуг. Фикс: обновить URL тайлов, сделать `pkk_client.py` явным по отношению к нерабочему редиректу, обновить тесты.

---

## Problem Frame

Два симптома одной причины:
1. **Тайлы не грузятся**: `PKK_TILES` и `OPENINFRA_TILES` указывают на `pkk.rosreestr.ru` → браузер получает HTML вместо PNG.
2. **Кадастровый поиск не возвращает результатов**: `pkk_client.py` следует 301 на НСПД SPA → `resp.json()` бросает `JSONDecodeError` → `except Exception → None` → `map_search` возвращает `cadastre: []`.

Тайлы НСПД работают: `nspd.gov.ru/arcgis/rest/services/PKK6/…/MapServer/tile/{z}/{y}/{x}` → HTTP 200, PNG ~15 KB. Геометрия участков через API НСПД недоступна без сессии (403). Это известное ограничение — деградация до `None` с `CircleMarker`-fallback остаётся текущим поведением.

---

## Requirements

- **R1**: Кадастровая сетка и ЗОУИТ-слои отображаются на карте (тайлы с НСПД)
- **R2**: `pkk_client.py` не делает лишний HTTP-запрос вслед за 301-редиректом
- **R3**: При недоступности API геометрия тихо возвращает `None` (без лога)
- **R4**: Тесты покрывают 301-редирект как сценарий деградации

---

## Key Technical Decisions

**KTD-1: Тайл-URL — НСПД, путь PKK6 без изменений**
НСПД сохранил путь `/arcgis/rest/services/PKK6/CadastreObjects/MapServer/tile/{z}/{y}/{x}` и `/PKK6/ZuitsPolygons/…`. Меняется только домен (`pkk.rosreestr.ru` → `nspd.gov.ru`). ArcGIS REST tile endpoint — не WMS — кеш браузера работает.

**KTD-2: `follow_redirects=False` + `is_success` check в pkk_client.py**
Вместо `raise_for_status()` после следования редиректу — явная проверка `resp.is_success` (True только для 2xx) при `follow_redirects=False`. Это останавливает цепочку на 301, не тратит время на загрузку HTML-SPA, возвращает `None` без исключения.

**KTD-3: Таймаут снизить с 5.0 до 3.0 сек**
С `follow_redirects=False` второй запрос к НСПД не происходит — ответ быстрее. 3 сек достаточно для внутрисетевого запроса к pkk.rosreestr.ru (который отвечает 301 за ~40 мс).

**KTD-4: Геометрия остаётся недоступной — коммерческий API отложен**
НСПД API требует сессию Госуслуг, которую нельзя автоматизировать. `rosreestr2coord` и аналоги бьют тот же API. Коммерческие альтернативы (`cadaste.ru`, `cadapi.ru`) — в `Deferred to Follow-Up Work`. Текущий fallback `CircleMarker` в `CadastralHighlight.tsx` остаётся.

---

## Scope Boundaries

### In scope
- Обновление URL тайлов в `tile-providers.ts`
- Рефактор `pkk_client.py`: `follow_redirects=False`, `is_success` check, таймаут
- Тест 301-редиректа в `test_pkk_client.py`

### Deferred to Follow-Up Work
- Интеграция коммерческого API для получения полигонов (cadaste.ru / cadapi.ru)
- Поле `cadastral_number` на модели `SupplyObject` (см. requirements doc)
- ЗОУИТ-пересечение по вектору (не тайлы)
- UI-индикатор "геометрия недоступна" в `MapSearchBar`

### Out of scope
- Авторизация через Госуслуги / сессии НСПД
- Любые другие карточные сервисы (Яндекс, 2ГИС)

---

## Implementation Units

### U1. Обновить URL тайлов кадастрового слоя и ЗОУИТ

**Goal:** Заменить домен `pkk.rosreestr.ru` на `nspd.gov.ru` в константах тайл-провайдеров.

**Requirements:** R1

**Dependencies:** нет

**Files:**
- `frontend/src/shared/ui/map/tile-providers.ts`

**Approach:**
В `PKK_TILES.url` и в `OPENINFRA_TILES` (ветка `MAP_ZOUIT_ENABLED = true`) заменить `pkk.rosreestr.ru` → `nspd.gov.ru`. Attribution обновить: `Росреестр ПКК` → `Росреестр НСПД`. Путь `/arcgis/rest/services/PKK6/…/MapServer/tile/{z}/{y}/{x}` остаётся неизменным.

**Patterns to follow:** Структура константы `PKK_TILES` в этом же файле.

**Test scenarios:**
- Тайлы загружаются на зуме 12-17 без ошибок сети (визуальная проверка в браузере)
- `MAP_ZOUIT_ENABLED = true` → URL содержит `nspd.gov.ru`
- `MAP_ZOUIT_ENABLED = false` → URL ЗОУИТ остаётся OpenRailwayMap (ветка не трогается)

**Verification:** Карта открывается, кадастровая сетка рисуется поверх базового слоя.

---

### U2. Рефактор pkk_client.py: явное детектирование редиректа

**Goal:** `pkk_client.py` перестаёт следовать за 301 → экономит запрос, возвращает `None` явно при нерабочем endpoint.

**Requirements:** R2, R3

**Dependencies:** нет

**Files:**
- `backend/apps/integration/pkk_client.py`

**Approach:**
1. Константа `_PKK_FEATURES_URL` остаётся `pkk.rosreestr.ru/api/features/1` — именно этот адрес исторически задокументирован; НСПД API не имеет аналогичного публичного endpoint.
2. В `httpx.AsyncClient(...)` добавить `follow_redirects=False`, снизить `_TIMEOUT = 3.0`.
3. Заменить `resp.raise_for_status()` + `resp.json()` на:
   - `if not resp.is_success: return None`  
   - `data = resp.json()`
4. Существующий `except Exception: return None` остаётся как финальный catch-all.

**Patterns to follow:** `apps/integration/dadata_client.py` — аналогичный паттерн httpx-клиента с graceful degradation.

**Test scenarios:**
- HTTP 301 от сервера → `search_cadastre` возвращает `None`
- HTTP 403 от сервера → `search_cadastre` возвращает `None`
- HTTP 200 + не-JSON тело → `search_cadastre` возвращает `None` (JSONDecodeError → except)
- HTTP 200 + валидный JSON с features → возвращает `CadastreResult` (существующий happy-path тест)
- Таймаут → `None` (существующий тест)

**Verification:** `docker compose exec backend pytest apps/integration/tests/test_pkk_client.py -v` — all pass.

---

### U3. Обновить тесты pkk_client — покрыть 301 и non-success

**Goal:** Явное тест-покрытие для сценариев деградации (301, 403), чтобы рефактор не сломался при следующем изменении.

**Requirements:** R4

**Dependencies:** U2

**Files:**
- `backend/apps/integration/tests/test_pkk_client.py`

**Approach:**
Добавить два новых теста:
1. `test_search_cadastre_redirect_returns_none` — мок возвращает `MagicMock(is_success=False, status_code=301)`.
2. `test_search_cadastre_forbidden_returns_none` — мок возвращает `MagicMock(is_success=False, status_code=403)`.

Существующий тест `test_search_cadastre_http_error_returns_none` (через `HTTPStatusError`) остаётся — он тестирует отдельный путь (исключение при получении ответа, не нерабочий статус).

**Patterns to follow:** Существующий стиль в `test_pkk_client.py` — `MagicMock` + `patch("apps.integration.pkk_client.httpx.AsyncClient")`.

**Test scenarios:**
- Мок status_code=301 + is_success=False → `None`
- Мок status_code=403 + is_success=False → `None`
- Все существующие тесты проходят без изменений

**Verification:** `pytest apps/integration/tests/test_pkk_client.py -v` — 7 passed (5 старых + 2 новых).

---

## Risks & Dependencies

| Риск | Вероятность | Митигация |
|---|---|---|
| НСПД изменит путь тайлов (PKK6) | Низкая | Тайлы работают >2 месяцев на текущем URL; мониторинг через `tileerror` event в MapView |
| pkk.rosreestr.ru перестанет отвечать совсем (не 301, а DNS/timeout) | Средняя | `except Exception → None` уже ловит; таймаут 3s не блокирует map_search |
| НСПД откроет /api/features аналог в будущем | Средняя | Константа `_PKK_FEATURES_URL` останется точкой изменения в одном месте |

---

## Sources & Research

- Диагностика (2026-06-17): `curl -skL pkk.rosreestr.ru` → 301 → `nspd.gov.ru/map?thematic=PKK`
- Тайл НСПД: `curl -sk nspd.gov.ru/arcgis/rest/services/PKK6/CadastreObjects/MapServer/tile/15/10427/5011` → HTTP 200, 15 116 bytes
- НСПД API: все `/api/*` endpoints → HTTP 403 `{"code": 403, "message": "Forbidden"}`
- OSM Wiki (публичная кадастровая карта): не содержит актуальных API endpoint'ов для получения геометрии
