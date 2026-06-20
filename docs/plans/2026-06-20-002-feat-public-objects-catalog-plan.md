---
title: "feat: Public Object Showcase — каталог «Наши объекты»"
date: 2026-06-20
status: active
sprint: 5
origin: docs/brainstorms/2026-06-20-erp-object-showcase-requirements.md
domains:
  - objects
  - frontend_public
---

# feat: Public Object Showcase — каталог «Наши объекты»

## Summary

Публичный раздел «Наши объекты» на сайте компании: оператор помечает объект как публичный, добавляет обложку и slug, посетитель видит фильтруемый каталог и детальные страницы с фото, описанием, адресом, мини-картой. Раздел поддерживает воронку заявок — `LeadForm` присутствует на каждой детальной странице.

## Problem Frame

`SupplyObject` живёт в ERP, но компания хочет показывать выполненные объекты потенциальным заказчикам на публичном сайте. Нет механизма выборки, SEO-идентификации (`slug`), управления обложкой и публикации на сайт. Раздел не нужен без фото и красивых URL — именно поэтому `cover_image_key` и `slug` входят в скоп одного спринта.

## Requirements

| ID | Требование |
|----|-----------|
| R1 | Оператор переключает `is_public` независимо от `status` объекта |
| R2 | Slug генерируется из `name` при создании/изменении; оператор может переопределить |
| R3 | Оператор прикрепляет обложку через существующий files pipeline (ClamAV уже отработал) |
| R4 | Публичный API возвращает только объекты с `is_public=True` для `PUBLIC_SITE_TENANT_ID` |
| R5 | `GET /api/v1/public/objects/` — каталог с фильтром по `resource_type`, cursor pagination |
| R6 | `GET /api/v1/public/objects/{slug}/` — детальная страница; 404 если `is_public=False` |
| R7 | PublicObjectSchema НЕ раскрывает: `status`, manager/foreman IDs, contract/contragent IDs, финансы, `tenant_id` |
| R8 | Frontend: `/objects/` и `/objects/[slug]/` в группе `(public)` |
| R9 | Мини-карта — только если `location` задан |

---

## Key Technical Decisions

### KTD-1: URL публичного API — `/api/v1/public/objects/`

`AsgiTenantEnforcementMiddleware` (common/tenancy/middleware.py) инжектирует `PUBLIC_SITE_TENANT_ID` только для путей с префиксом `PUBLIC_API_PREFIX = "/api/v1/public/"`. Путь `/api/v1/objects/public/` попал бы в authenticated zone. Новый роутер регистрируется в `config/urls.py` рядом с существующим public_router: `api.add_router("/public/objects/", public_objects_router)`.

### KTD-2: Slug — сервисный слой, не `pre_save` сигнал

`_generate_unique_slug(name, tenant_id, exclude_id=None)` размещается в `apps/objects/services/object_service.py` и вызывается из `ObjectCreateService` и `ObjectUpdateService`. Следует паттерну проекта: сигналы не используются для бизнес-логики. Тестируется напрямую без overhead сигнала.

Алгоритм (directional):
```
base = slugify(name)
candidate = base
n = 2
while SupplyObject.objects.filter(tenant_id=t, slug=candidate, is_deleted=False).exclude(id=exclude_id).exists():
    candidate = f"{base}-{n}"
    n += 1
return candidate
```

### KTD-3: Slug — nullable в Sprint #5, NOT NULL + unique в Release N+1

Sprint #5 (Release N): `slug = SlugField(null=True, blank=True, max_length=255)`. Backfill существующих объектов inline в `RunPython` data migration (~100 строк, Celery излишен). Unique constraint отсутствует.

Release N+1 (следующий спринт): `CREATE UNIQUE INDEX CONCURRENTLY (tenant_id, slug)` → `NOT VALID` constraint → `VALIDATE CONSTRAINT`. Отдельная миграция по Expand & Contract.

### KTD-4: cover_image_key — plain S3 key; StorageBackend Protocol переезжает в `apps/common`

`cover_image_key: CharField(null=True, blank=True, max_length=500)` хранит S3 object key. Публичный API генерирует download URL через `StorageBackend.generate_download_url()`.

Проблема: импортировать `StorageBackend` из `apps/files/services/storage_backend.py` в `apps/objects` — нарушение ADR-0005. Решение: Protocol `StorageBackend` переносится в `apps/common/contracts/storage_contracts.py`; `apps/files` импортирует его оттуда; `apps/objects` тоже импортирует из `apps/common`. Это правильное место для cross-domain Protocol (как `FinanceTransactionFinder`).

Оператор: загружает файл через files pipeline → frontend читает `s3_key` из FileRecord detail response → PATCH SupplyObject с `cover_image_key`. Нет дополнительных API endpoints, нет дублирования файлов.

### KTD-5: Tenant scoping публичных запросов — без дополнительного кода

`ObjectRepository.get_queryset()` использует `get_tenant_id()` из context. Middleware инжектирует правильный tenant для путей `/api/v1/public/`. Публичные query функции добавляют только `filter(is_public=True)` поверх стандартного queryset.

### KTD-6: `status` в PublicObjectSchema отсутствует

Внутреннее поле ERP. Для посетителя достаточно: название, описание, фото, адрес, тип ресурса/объекта, карта.

---

## Scope Boundaries

### В скопе Sprint #5
- Поля `is_public`, `slug`, `cover_image_key` на `SupplyObject` + expand migration + slug backfill
- StorageBackend Protocol в `apps/common/contracts/`
- Публичный API (2 endpoint-а, без аутентификации)
- Operator dashboard UI: is_public toggle, slug editor, cover image picker
- Frontend: `/objects/` каталог + `/objects/[slug]/` деталь

### Deferred to Follow-Up Work
- Unique constraint + NOT NULL на `slug` (Release N+1, отдельная миграция)
- SEO: OpenGraph, Schema.org, sitemap для объектов
- Gallery — несколько фото на объект
- Cross-domain FileRecord validation (`FileInfoProtocol`) для verify AVAILABLE status
- Related objects («Другие объекты по типу ресурса»)

### Вне скопа
- GIS: ЗОУИТ, кадастральная привязка
- Отзывы, комментарии, лиды из карточки объекта (глобальный LeadForm покрывает)

---

## High-Level Technical Design

### Request flow — публичный посетитель

```
Browser
  │
  ├─ GET /api/v1/public/objects/?resource_type=GAS
  │         │
  │  AsgiTenantEnforcementMiddleware
  │  path starts with /api/v1/public/ → set_tenant_id(PUBLIC_SITE_TENANT_ID)
  │         │
  │  apps/objects/api_public.py  (public_objects_router, no @require_permission)
  │         │
  │  get_public_objects_catalog(resource_type="GAS")
  │         │
  │  ObjectRepository.get_queryset()           ← tenant_id from context
  │  .filter(is_public=True, resource_type="GAS")
  │         │
  │  [PublicObjectSchema]  — no status, no manager_id, no contract_id
  │         │
  └─ JSON 200
```

### Migration sequence

```
0005_add_public_fields       — is_public NOT NULL/default=False, slug nullable, cover_image_key nullable
0006_backfill_object_slugs   — RunPython: slugify(name) для всех tenant, без коллизий
[Next sprint]
0007_slug_unique_constraint  — CREATE UNIQUE INDEX CONCURRENTLY (tenant_id, slug)
                               ALTER TABLE ... ADD CONSTRAINT NOT VALID → VALIDATE
```

### PublicObjectSchema поля

| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | UUID | |
| `slug` | str | |
| `name` | str | |
| `address` | str | |
| `resource_type` | str | ELECTRICITY \| GAS \| WATER \| HEAT \| OTHER |
| `object_type` | str | BUILDING \| GRS \| PIPELINE \| … |
| `description` | str | |
| `cover_image_url` | str \| null | presigned S3 download URL или null |
| `location` | `{lat, lon}` \| null | null → карта не отображается |

---

## Implementation Units

### U1. SupplyObject: новые поля + expand migration

**Goal:** Добавить `is_public`, `slug`, `cover_image_key` к модели и БД.

**Requirements:** R1, R2, R3

**Dependencies:** —

**Files:**
- `backend/apps/objects/models/supply_object.py` (modify)
- `backend/apps/objects/migrations/0005_add_public_fields.py` (create)

**Approach:**
- `is_public = BooleanField(default=False)` — NOT NULL, DB default=false, безопасно в PG.
- `slug = SlugField(max_length=255, null=True, blank=True, db_index=True)` — nullable, без unique.
- `cover_image_key = CharField(max_length=500, null=True, blank=True)` — S3 object key.
- Миграция содержит `reverse_sql` / `state_operations` для backward (DROP COLUMN для всех трёх).

**Patterns to follow:** `backend/apps/objects/migrations/0004_object_contract.py`

**Test scenarios:**
- Миграция применяется forward и backward без ошибок.
- После forward: все существующие объекты имеют `is_public=False`, `slug=None`, `cover_image_key=None`.
- `SupplyObject(name="test", ...).save()` — `is_public` дефолтный False, не null.

**Verification:** `make migrate` зелёный; `SupplyObject._meta.get_field("is_public")` не бросает.

---

### U2. Slug generation в сервисном слое

**Goal:** Автоматически генерировать slug при создании/изменении названия; поддержать ручную установку.

**Requirements:** R2

**Dependencies:** U1

**Files:**
- `backend/apps/objects/services/object_service.py` (modify: add `_generate_unique_slug`, update Create/Update)
- `backend/apps/objects/schemas/object_dto.py` (modify: add `slug`, `is_public`, `cover_image_key` к request/response)
- `backend/apps/objects/tests/test_slug_generation.py` (create)

**Approach:**

`_generate_unique_slug(name, tenant_id, exclude_id=None)` — приватная функция в том же модуле. Использует `django.utils.text.slugify`. Проверяет коллизии через `SupplyObject.objects.filter(tenant_id=..., slug=candidate, is_deleted=False).exclude(id=exclude_id).exists()`.

`ObjectCreateService.execute()`: вызвать `_generate_unique_slug(body.name, get_tenant_id())` и записать в `obj.slug`.

`ObjectUpdateService.execute()`: если `body.slug` задан явно — использовать его. Иначе если `body.name` изменился — перегенерировать через `_generate_unique_slug(body.name, tenant_id, exclude_id=obj.id)`.

`SupplyObjectUpdateRequest`: добавить `slug: Optional[str] = None`, `is_public: Optional[bool] = None`, `cover_image_key: Optional[str] = None`. Добавить `field_validator` для slug (только буквы/цифры/дефисы, max 255).

`SupplyObjectDetailResponse`: добавить `slug: Optional[str] = None`, `is_public: bool = False`, `cover_image_key: Optional[str] = None`.

**Patterns to follow:** `backend/apps/objects/services/object_service.py` (ObjectUpdateService.execute паттерн); `backend/apps/contracts/services/contract_create_service.py` (field validation в Pydantic)

**Test scenarios:**
- Создание объекта с именем `"Подстанция Иванова"` → slug `"podstanciya-ivanova"`.
- Два объекта с одинаковым именем в одном tenant → slugs `"test"` и `"test-2"`.
- Два объекта с одинаковым именем в разных tenant-ах → оба `"test"` (нет конфликта).
- `ObjectUpdateService` с изменением name → slug перегенерирован.
- `ObjectUpdateService` с явным `slug="my-custom"` → сохранён без перегенерации.
- `ObjectUpdateService` с изменением `is_public=True` → объект помечен публичным.
- `ObjectUpdateService` с `cover_image_key="objects/abc123.jpg"` → поле сохранено.
- Пустая строка в slug → валидатор отклоняет.

**Verification:** `make test-objects` зелёный; все тест-сценарии покрыты.

---

### U3. Slug backfill data migration

**Goal:** Установить slug для всех существующих SupplyObject с `slug=None`.

**Requirements:** R2

**Dependencies:** U2

**Files:**
- `backend/apps/objects/migrations/0006_backfill_object_slugs.py` (create)

**Approach:**
- `RunPython(backfill_slugs, reverse_code=migrations.RunPython.noop)`.
- `backfill_slugs(apps, schema_editor)`: для каждого tenant_id получить объекты с `is_deleted=False, slug=None`; применить ту же логику `slugify + суффикс` (реализованную inline в функции миграции, без импорта из сервиса). ~100 строк на 1 компанию — Celery избыточен.
- `reverse_code=migrations.RunPython.noop` — обратное действие невозможно, explicit declaration.

**Patterns to follow:** `backend/apps/common/migrations/0024_seed_files_delete_manager.py` (если есть RunPython пример)

**Test scenarios:**
- После migration: `SupplyObject.objects.filter(is_deleted=False, slug=None).count() == 0`.
- Два объекта с одинаковым именем в одном tenant → уникальные slugs с суффиксом.
- Объекты разных tenant-ов могут иметь одинаковые slugs — нет ложных суффиксов.
- Migration backward → noop (не ломает).

**Verification:** `make migrate` зелёный в тестовой среде; `select count(*) from objects_supply_objects where slug is null and is_deleted = false` = 0.

---

### U4. StorageBackend Protocol в `apps/common` + PublicObjectSchema + public query functions

**Goal:** ADR-0005-совместимый механизм генерации download URL + DTO + query functions для публичного API.

**Requirements:** R4, R5, R6, R7, R9

**Dependencies:** U1

**Files:**
- `backend/apps/common/contracts/storage_contracts.py` (create: Protocol `StorageBackend` + `get_storage_backend()` factory)
- `backend/apps/files/services/storage_backend.py` (modify: `StorageBackend` Protocol переезжает в common; файл остаётся как реализации `StubStorageBackend`, `S3StorageBackend` + импорт Protocol из common)
- `backend/apps/objects/schemas/object_dto.py` (modify: add `PublicObjectLocationSchema`, `PublicObjectSchema`, `PublicObjectListResponse`)
- `backend/apps/objects/queries/public_object_queries.py` (create)
- `backend/apps/objects/tests/test_public_object_queries.py` (create)

**Approach:**

`storage_contracts.py`:
```python
# Directional — not implementation code
class StorageBackend(typing.Protocol):
    def generate_download_url(self, *, object_key: str, expires_in: int) -> str: ...

def get_storage_backend() -> StorageBackend:
    # returns StubStorageBackend in test/dev, S3StorageBackend in prod
    # reads from django.conf.settings.STORAGE_BACKEND_CLASS
    ...
```

`public_object_queries.py`:
- `get_public_objects_catalog(resource_type=None, cursor=None, limit=20) -> dict` — `ObjectRepository.get_queryset().filter(is_public=True, slug__isnull=False)`, cursor by `id DESC`. Для каждого объекта с `cover_image_key` вызывает `storage.generate_download_url(object_key=cover_image_key, expires_in=3600)`.
- `get_public_object_by_slug(slug: str) -> PublicObjectSchema` — `filter(is_public=True, slug=slug).first()`, бросает `PermissionDenied` если None.

Slug filter: `filter(slug__isnull=False)` исключает объекты без slug (не все существующие объекты будут иметь slug сразу).

**Patterns to follow:** `backend/apps/objects/queries/object_queries.py`; `backend/apps/site/queries/public_queries.py`; `backend/apps/common/contracts/` (для структуры Protocol файла)

**Test scenarios:**
- Catalog: возвращает только `is_public=True` объекты.
- Catalog: `resource_type="GAS"` фильтрует правильно.
- Catalog: cursor pagination — второй запрос с cursor возвращает следующую страницу.
- Catalog: объекты из другого tenant не появляются (tenant из context).
- Catalog: объект с `cover_image_key` → `cover_image_url` непустой (mock storage).
- Catalog: объект без `cover_image_key` → `cover_image_url=None`.
- Catalog: объект без `location` → `location=None` в ответе.
- By slug: правильный объект для существующего slug.
- By slug: несуществующий slug → `PermissionDenied`.
- By slug: существующий slug с `is_public=False` → `PermissionDenied`.

**Verification:** `make test-objects` зелёный; StorageBackend mock инжектируется через dependency injection или `override_settings`.

---

### U5. Публичный API роутер

**Goal:** Два unauthenticated GET endpoint-а для каталога и детали.

**Requirements:** R4, R5, R6, R7

**Dependencies:** U4

**Files:**
- `backend/apps/objects/api_public.py` (create)
- `backend/config/urls.py` (modify: register `public_objects_router`)
- `backend/apps/objects/tests/test_public_objects_api.py` (create)

**Approach:**

```python
# apps/objects/api_public.py — directional
router = Router(tags=["public-objects"])

@router.get("/", response={200: PublicObjectListResponse})
def list_public_objects(request, resource_type: Optional[str] = Query(None), cursor: ...):
    ...

@router.get("/{slug}/", response={200: PublicObjectSchema, 404: dict})
def get_public_object(request, slug: str):
    try:
        return 200, get_public_object_by_slug(slug)
    except PermissionDenied:
        return 404, make_error_response("OBJECT_NOT_FOUND", "Объект не найден.")
```

Нет `@require_permission`. Нет auth. Tenant инжектируется middleware автоматически.

`config/urls.py`: добавить после существующего `api.add_router("/public/", public_router)`:
```python
from apps.objects.api_public import router as public_objects_router
api.add_router("/public/objects/", public_objects_router)
```

**Важно:** Все routes заканчиваются `/` — trailing slash convention (см. `docs/solutions/runtime-errors/django-ninja-trailing-slash-routing.md`).

**Patterns to follow:** `backend/apps/site/api/public_api.py`; `backend/apps/objects/api.py`

**Test scenarios:** Все тесты используют `override_settings(PUBLIC_SITE_TENANT_ID=...)` — паттерн из `backend/apps/site/tests/test_public_api.py`.
- `GET /api/v1/public/objects/` → 200 + список.
- `GET /api/v1/public/objects/?resource_type=GAS` → фильтр работает.
- `GET /api/v1/public/objects/existing-slug/` → 200 с данными объекта.
- `GET /api/v1/public/objects/non-existent/` → 404 в формате error envelope (rule 6.6).
- `GET /api/v1/public/objects/private-slug/` (is_public=False) → 404.
- Запрос без JWT header → проходит (нет аутентификации), tenant из settings.
- Объект другого tenant → не виден.
- Response не содержит полей `status`, `manager_id`, `contract_id`, `tenant_id`.

**Verification:** `make test-objects` зелёный.

---

### U6. Dashboard: оператор управляет публичностью

**Goal:** UI для управления `is_public`, `slug`, `cover_image_key` в карточке объекта.

**Requirements:** R1, R2, R3

**Dependencies:** U2, U5 (после U5 — regenerate TS SDK)

**Files:**
- `frontend/src/app/dashboard/objects/[id]/page.tsx` (modify)
- `frontend/src/shared/api/generated/` (regenerate SDK after U5 ships new endpoints and schema fields)

**Approach:**

**Регенерация SDK**: после добавления `slug`, `is_public`, `cover_image_key` в `SupplyObjectDetailResponse` и `SupplyObjectUpdateRequest` — SDK перегенерируется. Dashboard получит типизированные поля автоматически.

**В `[id]/page.tsx`:**
- Toggle `is_public` → `PATCH /api/v1/objects/{id}/` с `{is_public: boolean}` → optimistic update.
- Поле slug (read-only + кнопка «Редактировать»). Edit mode → input + сохранение через PATCH с `{slug: string}`.
- Cover image секция: текущее превью (из presigned URL полученного через `GET /api/v1/files/{file_id}/download-url/` или напрямую из FileRecord `s3_key`) + кнопка «Загрузить обложку». Upload flow: `POST /api/v1/files/` (upload init) → upload to S3 → poll `GET /api/v1/files/{id}/` до `status=AVAILABLE` → PATCH объекта с `{cover_image_key: s3_key}`.
- Placeholder если `cover_image_key=null` — нет broken image.

**Patterns to follow:** Existing PATCH mutation patterns в `frontend/src/app/dashboard/objects/[id]/page.tsx`; LeadForm component для upload UX reference

**Test scenarios:**
- Toggle `is_public` → PATCH 200 → состояние отображается корректно.
- Редактирование slug → PATCH 200 → новый slug виден.
- Upload обложки → polling до AVAILABLE → PATCH объекта → превью появляется.
- При `cover_image_key=null` — placeholder (не broken img tag).

**Execution note:** Проверить browser-qa на golden path оператора перед задачей «done»: включить публикацию, загрузить обложку, убедиться что объект виден на `/objects/`.

**Verification:** Browser QA — operator flow; `make test-objects` не ломается.

---

### U7. Публичный сайт: каталог и детальная страница

**Goal:** `/objects/` и `/objects/[slug]/` в группе `(public)`.

**Requirements:** R8, R9

**Dependencies:** U5

**Files:**
- `frontend/src/shared/api/public-objects.ts` (create)
- `frontend/src/app/(public)/objects/page.tsx` (create)
- `frontend/src/app/(public)/objects/[slug]/page.tsx` (create)

**Approach:**

`public-objects.ts` — raw fetch без auth (паттерн из `services/[slug]/page.tsx`):
```typescript
// Directional — not implementation specification
const BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://backend:8000'

fetchPublicObjects(resourceType?: string): Promise<PublicObjectListResponse>
fetchPublicObject(slug: string): Promise<PublicObjectSchema | null>
```

**`/objects/page.tsx`** (Server Component):
- Фильтр-чипы по `resource_type` (ALL / ELECTRICITY / GAS / WATER / HEAT) — query param.
- Сетка карточек: обложка (`<img src={cover_image_url}>`), placeholder SVG если null, название, адрес, бейдж типа ресурса.
- Ссылка `/objects/{slug}` для каждой карточки.
- `cache: 'no-store'` — данные свежие (операторы публикуют в реальном времени).

**`/objects/[slug]/page.tsx`** (Server Component):
- `fetchPublicObject(slug)` → `notFound()` если null.
- Обложка + название + описание + адрес + тип ресурса/объекта.
- Мини-карта (`dynamic(() => import(...), { ssr: false })`) — только если `location !== null`. Тот же Leaflet компонент что в дашборде (`LocationSection`), stripped от edit controls.
- `<LeadForm />` в конце страницы (`(public)/components/LeadForm.tsx`).

**Patterns to follow:** `frontend/src/app/(public)/services/[slug]/page.tsx`; `frontend/src/app/dashboard/objects/[id]/components/LocationSection.tsx` (Leaflet component)

**Test scenarios:**
- `/objects/` рендерится, возвращает 200.
- `/objects/?resource_type=GAS` — фильтр передаётся в fetch, только GAS объекты.
- `/objects/existing-slug` — 200, данные объекта.
- `/objects/non-existent` → `notFound()` → Next.js 404 page.
- Объект без `location` — карта не рендерится (нет Leaflet container в DOM).
- Объект без `cover_image_url` — placeholder img, нет broken image.
- `<LeadForm />` присутствует на детальной странице.
- Leaflet с `dynamic + ssr:false` — нет SSR error (`window is not defined`).

**Verification:** browser-qa — golden path посетителя: открыть каталог → выбрать объект → убедиться в наличии формы заявки.

---

## System-Wide Impact

- **`config/urls.py`**: новая строка `api.add_router("/public/objects/", ...)` — влияет на OpenAPI schema и TS SDK.
- **TS SDK (`frontend/src/shared/api/generated/`)**: регенерация обязательна после U5. Новые поля в `SupplyObjectDetailResponse`/`SupplyObjectUpdateRequest` + новые публичные endpoints. Выполнять `make generate-sdk` (или аналог) перед U6.
- **`apps/common/contracts/`**: добавляется `storage_contracts.py` — используется `apps/objects`, обратно совместимо для `apps/files`.
- **`PUBLIC_SITE_TENANT_ID`**: уже обязателен и валидируется при старте (`ImproperlyConfigured` если не задан). Документировать в deployment runbook как prerequisite для Sprint #5.

---

## Deployment Requirements

`PUBLIC_SITE_TENANT_ID=<UUID>` — обязательная env переменная. `AsgiTenantEnforcementMiddleware._validate_public_site_settings()` бросает `ImproperlyConfigured` на старте сервера при отсутствии. Новые endpoint-ы не поднимутся без неё.

S3 credentials — необходимы для `cover_image_url` в продакшене. При отсутствии backend: `StubStorageBackend` вернёт stub URL в dev/test; graceful null fallback если S3 недоступен — деталь реализации.

---

## Open Questions

| # | Вопрос | Рекомендация |
|---|--------|-------------|
| OQ-1 | Presigned URL TTL для обложки? | 3600s (1 час). Если bucket публичный — прямой URL, presigned не нужен. Определяется при просмотре S3 конфигурации. |
| OQ-2 | Slug unique constraint — в Sprint #5 или следующий спринт? | Следующий спринт. Не усложнять Sprint #5. |
| OQ-3 | Leaflet в public detail — reuse `LocationSection` компонент или новый stripped вариант? | Reuse с пробросом `readOnly=true` prop, если компонент это поддерживает. Иначе — stripped копия без edit controls. |

---

## Deferred Implementation Details

- Точный API `get_storage_backend()` factory (settings key, env var name) — определяется при чтении `apps/files/services/` конфигурации.
- Дизайн placeholder-а для обложки (SVG иконка по `resource_type` или цветной градиент) — решение при реализации U7.
- Точный путь к SDK regeneration команде (`make generate-sdk` или `docker compose exec frontend npx ...`) — определяется при чтении `Makefile`.
- `STORAGE_BACKEND_CLASS` setting name — согласовать с существующей конфигурацией в `apps/files`.
