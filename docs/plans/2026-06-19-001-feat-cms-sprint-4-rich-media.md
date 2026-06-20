---
title: "CMS Sprint #4 — Rich Media: Gallery UI + Content Versioning"
date: 2026-06-19
status: completed
branch: feat/public-site-cms-portal
module: site
tags: [cms, gallery, versioning, dnd-kit, upload, sprint-4]
---

# CMS Sprint #4 — Rich Media

## Scope

Два feature-блока:

1. **Gallery UI** — полный CRUD для `SiteImage`, drag-and-drop reorder (@dnd-kit), bulk multi-file upload (presigned S3), caption/alt editing. Галерея привязана к `SitePageContent` через `section`.
2. **Content Versioning** — модель `SitePageRevision` (до 20 снимков на страницу), API list/rollback, UI история версий.

## Scope Boundaries

- **Не входит**: интеграция `SiteImage` с Files domain (ClamAV pipeline, `FileRecord`). Это Sprint #5 или отдельный ADR (Files Scan Lifecycle). В этом спринте добавляем nullable `file_record_id: UUID` как hook, но не wire up.
- **Не входит**: `@tiptap/extension-image` (embedded images в Tiptap — отдельная фича).
- **Не входит**: изменение механизма хранения `SiteImage.s3_key` — он остаётся строкой.

## Current State (verified)

### Backend
- `SiteImage` (`site_site_images`): `s3_key, section, caption, order` — нет `alt`, нет `file_record_id`
- Gallery API: только `GET /gallery/` + `POST /gallery/` (через query params, не body)
- `SitePageContent.version` — OCC-счётчик, инкремент не работает (`update_or_create()` вместо `atomic_cas_update()`)
- `SitePageRevision`: не существует
- `CmsUpdateService`: `update_or_create()` → нарушает OCC-контракт

### Frontend
- `@dnd-kit` не установлен
- `useUploadFile()` существует в `frontend/src/shared/api/files.ts` — presigned S3 + SHA-256, один файл
- Нет `useDeleteGalleryImage()`, `useUpdateGalleryImage()`, `useReorderGallery()`
- Нет Gallery UI компонента
- Нет Version History UI

## Architecture Decisions

### A1. SiteImage upload strategy
`POST /api/v1/cms/gallery/request-upload/` возвращает presigned S3 PUT URL (прямой паттерн из Files domain). После PUT клиент подтверждает через `POST /api/v1/cms/gallery/confirm-upload/` с `s3_key` + метаданными. `SiteImage` создаётся при confirm, не при request. Это изолирует незавершённые upload-ы от галереи.

**Почему не через Files domain**: ADR для Files Scan Lifecycle ещё не принят. Подключение к `FileRecord` + ClamAV в этом спринте нарушало бы принцип Expand & Contract — сначала нужен ADR, потом миграция. `file_record_id` добавляется как nullable поле для будущего bridge.

### A2. Reorder strategy
`PATCH /api/v1/cms/gallery/{id}/` принимает `order: int`. Bulk reorder через `POST /api/v1/cms/gallery/reorder/` с `[{id, order}]`. Клиент отправляет полный новый порядок после drop, бэкенд atomic-обновляет все записи в транзакции.

### A3. Versioning trigger
Ревизия создаётся **до** каждого `PATCH /cms/pages/{slug}/` (автосохранение снимка текущего состояния). Rollback — атомарный: восстанавливает `content + meta` в `SitePageContent`, создаёт новую ревизию с меткой `"rollback from #N"`.

### A4. Revision limit
Максимум 20 ревизий на `(tenant_id, page_id)`. При создании 21-й — удаляется самая старая. Реализуется через `DELETE WHERE id IN (SELECT ... ORDER BY revision_number ASC LIMIT 1)` внутри `create_revision()`. Нет отдельного Celery job.

### A5. CmsUpdateService OCC fix
`CmsUpdateService.update()` переключается с `update_or_create()` на `EnterpriseSreRepository.atomic_cas_update()`. `CmsCreateService` остаётся на `create()`. Это исправляет нарушение из текущей реализации.

## Implementation Units

### U1: Gallery Backend (API + Migration)

**Goal**: полный CRUD `SiteImage` + presigned upload endpoint + reorder

**Files**:
- Modify: `backend/apps/site/models/site_image.py` — добавить `alt: CharField(200, blank=True)`, `file_record_id: UUIDField(null=True, blank=True, db_index=True)`
- Modify: `backend/apps/site/schemas/cms_dto.py` — `CmsImageResponse` (добавить `alt`, `file_record_id`), `GalleryReorderRequest`, `PresignedUploadRequest`, `PresignedUploadResponse`, `GalleryConfirmRequest`
- Modify: `backend/apps/site/api/cms_api.py` — 4 новых endpoint (см. ниже)
- Modify: `backend/apps/site/services/cms_services.py` — `GalleryService.request_upload()`, `confirm_upload()`, `update_image()`, `delete_image()`, `reorder()`
- Create: `backend/apps/site/migrations/0003_siteimage_alt_file_record_id.py`
- Modify: `backend/apps/site/tests/test_cms_api.py`

**New endpoints**:
```
POST   /api/v1/cms/gallery/request-upload/   → {upload_url, s3_key, expires_at}
POST   /api/v1/cms/gallery/confirm-upload/   → CmsImageResponse
PATCH  /api/v1/cms/gallery/{image_id}/       → CmsImageResponse
DELETE /api/v1/cms/gallery/{image_id}/       → 204
POST   /api/v1/cms/gallery/reorder/          → {updated: int}
```

**Patterns to follow**:
- `backend/apps/files/services/upload_init_service.py` — паттерн presigned URL через boto3
- `backend/apps/site/api/cms_api.py` — существующий router, RBAC checks
- `backend/apps/common/repositories/base_repository.py` — `atomic_cas_update()`

**Migration rules** (ADR-0002 Expand & Contract):
- Все новые поля nullable — никаких `NOT NULL` без backfill в следующем релизе
- Без `CONCURRENTLY` для простых `AddField` (Django auto)

**Test scenarios**:
- `DELETE /gallery/{id}/` с `write:cms` → 204; без permission → 403; несуществующий → 404; чужой tenant → 404
- `PATCH /gallery/{id}/` → обновляет caption/alt/order; другой tenant → 404
- `POST /gallery/reorder/` → порядок обновляется атомарно; незнакомые id из другого tenant → игнорируются (фильтр по tenant)
- `POST /gallery/request-upload/` → возвращает presigned URL с истечением; `POST /gallery/confirm-upload/` → создаёт `SiteImage` с корректным `s3_key`
- `GET /gallery/?section=about` — пагинация, сортировка по `order`

**Verification**: все 5 тестовых сценариев passing; `ruff check .` чист; `make migrate` успешен.

---

### U2: Content Versioning Backend

**Goal**: `SitePageRevision` модель + CRUD service + API list/rollback + OCC fix в UpdateService

**Files**:
- Create: `backend/apps/site/models/site_page_revision.py`
- Modify: `backend/apps/site/models/__init__.py` — reexport
- Modify: `backend/apps/site/schemas/cms_dto.py` — `RevisionResponse`, `RevisionListResponse`
- Modify: `backend/apps/site/api/cms_api.py` — 2 новых endpoint
- Modify: `backend/apps/site/services/cms_services.py` — `CmsRevisionService`, fix `CmsUpdateService`
- Create: `backend/apps/site/migrations/0004_sitepagerevision.py`
- Create: `backend/apps/site/tests/test_cms_versioning.py`

**Model `SitePageRevision`** (`BaseEnterpriseModel`):
```python
class SitePageRevision(BaseEnterpriseModel):
    class Meta:
        db_table = "site_page_revisions"
        indexes = [
            models.Index(fields=["tenant_id", "page_id", "-revision_number"]),
        ]
    
    page_id: UUIDField(db_index=True)  # не FK — tenant isolation
    revision_number: PositiveIntegerField()
    content: JSONField()
    meta: JSONField()
    status: CharField(20)  # snapshot статуса на момент сохранения
    created_by_id: UUIDField()
    label: CharField(200, blank=True, default="")  # "rollback from #N", "auto", etc.
    
    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["tenant_id", "page_id", "revision_number"],
                name="uniq_revision_per_page"
            )
        ]
```

**`CmsRevisionService`**:
- `create_revision(page: SitePageContent, created_by_id: UUID, label: str = "auto") → SitePageRevision` — в `transaction.atomic()`, trim oldest if count > 20
- `list_revisions(page_id, tenant_id, cursor, limit=20) → list[SitePageRevision]` — cursor по `revision_number DESC`
- `rollback(page_id, revision_id, tenant_id, user_id) → SitePageContent` — restore content/meta/status, `create_revision()` с label `f"rollback from #{src.revision_number}"`

**New endpoints**:
```
GET  /api/v1/cms/pages/{slug}/revisions/                     → RevisionListResponse (cursor)
POST /api/v1/cms/pages/{slug}/revisions/{revision_id}/rollback/ → CmsPageContentResponse
```

**`CmsUpdateService` OCC fix**:
- Заменить `update_or_create()` на `atomic_cas_update()` для существующих страниц
- Вызывать `CmsRevisionService.create_revision()` до обновления (pre-update snapshot)
- `version` должен правильно инкрементироваться через CAS

**Test scenarios**:
- `PATCH /pages/{slug}/` → создаётся ревизия с `revision_number=1`, повторный PATCH → `revision_number=2`
- При 21-м сохранении — самая старая ревизия удалена, остаётся ровно 20
- `GET /revisions/` → cursor pagination, сортировка по убыванию `revision_number`
- Rollback: контент и мета восстанавливаются точно; создаётся ревизия с меткой; старый `revision_number` увеличивается
- Rollback чужой страницы → 404; несуществующей revision_id → 404
- OCC: concurrent PATCH с одним `version` → второй получает 409

**Verification**: все сценарии passing; `CmsUpdateService` тест проверяет инкремент `version`.

---

### U3: Gallery UI Frontend

**Goal**: компонент галереи с drag-and-drop reorder, multi-upload, inline редактирование

**Files**:
- Modify: `frontend/package.json` — добавить `@dnd-kit/core @dnd-kit/sortable @dnd-kit/utilities`
- Create: `frontend/src/shared/ui/cms/GalleryManager.tsx`
- Create: `frontend/src/shared/ui/cms/GalleryGrid.tsx` — `@dnd-kit/sortable` SortableContext
- Create: `frontend/src/shared/ui/cms/GalleryItem.tsx` — thumbnail, caption input, delete btn
- Create: `frontend/src/shared/ui/cms/GalleryUploadZone.tsx` — drag-and-drop file drop + file input
- Modify: `frontend/src/shared/ui/cms/index.ts` — reexport
- Modify: `frontend/src/shared/api/cms.ts` — новые hooks: `useDeleteGalleryImage`, `useUpdateGalleryImage`, `useReorderGallery`, `usePresignedUpload`, `useConfirmUpload`
- Modify: `frontend/src/app/dashboard/cms/[section]/page.tsx` — добавить `<GalleryManager section={section} />`

**Multi-upload approach**: `Promise.all(selectedFiles.map(f => uploadSingle(f)))` через существующий `useUploadFile()`. Показывать progress per-file через локальный state.

**Reorder**: `@dnd-kit/sortable` `SortableContext` + `useSortable`. На `DragEndEvent` — optimistic update locale order → `useReorderGallery.mutate([{id, order}...])` с rollback на ошибке.

**Patterns to follow**:
- `frontend/src/shared/api/files.ts` — `useUploadFile()` паттерн presigned upload
- `frontend/src/app/dashboard/files/page.tsx` — file input ref паттерн
- `frontend/src/shared/ui/cms/StatusWidget.tsx` — shadcn/ui, TanStack Query mutation

**Test scenarios** (browser-qa):
- Drag-and-drop файла в dropzone → появляется прогресс → thumbnail
- Выбор нескольких файлов через input → все загружаются параллельно
- Drag-and-drop reorder → новый порядок persists после reload
- Удаление изображения → оптимистично исчезает; отмена если API вернул ошибку
- Редактирование caption inline → debounced PATCH

**Verification**: `npm run build` без ошибок; TypeScript strict; `browser-qa` golden path пройден.

---

### U4: Version History UI Frontend

**Goal**: drawer/панель истории ревизий с rollback action

**Files**:
- Create: `frontend/src/shared/ui/cms/RevisionHistoryDrawer.tsx`
- Create: `frontend/src/shared/ui/cms/RevisionItem.tsx`
- Modify: `frontend/src/shared/api/cms.ts` — `usePageRevisions(slug, cursor?)`, `useRollbackRevision(slug)`
- Modify: `frontend/src/shared/ui/cms/StatusWidget.tsx` — кнопка "История" открывает drawer
- Modify: `frontend/src/shared/ui/cms/index.ts` — reexport

**RevisionHistoryDrawer**:
- Infinite scroll через TanStack Query `useInfiniteQuery` (cursor pagination)
- Каждая ревизия: номер, datetime, label, кнопка "Откатить"
- Rollback → confirmation dialog → `useRollbackRevision.mutate()` → invalidate `useCmsPage(slug)` → drawer закрывается
- Пустое состояние если нет ревизий

**Patterns to follow**:
- `frontend/src/shared/ui/cms/MetaEditor.tsx` — shadcn/ui, формат компонентов
- TanStack Query `useInfiniteQuery` для cursor pagination

**Test scenarios** (browser-qa):
- Кнопка "История" открывает drawer со списком ревизий
- Список подгружается при скролле (infinite scroll)
- "Откатить" → dialog → подтверждение → контент редактора обновляется
- Пустое состояние на новой странице без правок

**Verification**: `npm run build` без ошибок; `browser-qa` rollback flow пройден.

---

## Dependencies & Sequencing

```
U1 (Gallery Backend) ──────────────────── U3 (Gallery UI)
U2 (Versioning Backend) ──────────────── U4 (History UI)
```

U1 и U2 параллельны. U3 зависит от U1 (API). U4 зависит от U2 (API).

## Risks

| Риск | Вероятность | Митигация |
|---|---|---|
| `atomic_cas_update()` нарушает существующие тесты CMS (были написаны под `update_or_create()`) | Средняя | Обновить тесты одновременно с сервисом |
| `@dnd-kit` конфликт версий с React 19 | Низкая | Проверить `peerDependencies` перед install |
| Presigned URL expiry — клиент медленно загружает большой файл | Средняя | `ExpiresIn=3600` (1 час), клиент не должен кешировать URL |
| SiteImage.order коллизии при concurrent reorder | Низкая | Reorder обновляет весь набор в одной транзакции |

## Definition of Done

- [ ] U1: 5 новых endpoints работают, тесты проходят, миграция применяется
- [ ] U2: `SitePageRevision` создаётся при каждом PATCH страницы, rollback восстанавливает контент, 409 на OCC конфликте
- [ ] U3: gallery рендерится, multi-upload работает, drag-and-drop persists
- [ ] U4: история отображается, rollback через UI работает
- [ ] `make test-all` → 0 failed
- [ ] `ruff check .` → 0 errors
- [ ] `npm run build` → 0 errors
- [ ] `browser-qa` golden path: Gallery upload → reorder → Version rollback
