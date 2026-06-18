---
date: 2026-06-17
topic: cms-public-site-sprint-3
focus: CMS для публичного сайта — Главная, О компании, Услуги, Контакты. Редактирование из ERP.
mode: repo-grounded
---

# Ideation: CMS публичного сайта — Sprint #3

## Grounding Context

**Стек:** Django 5.x + Next.js 15 + Tiptap (ProseMirror JSON) + PostgreSQL 16

**Уже реализовано (U1–U11):** `SitePageContent(slug, content JSON, meta JSON)`, `SiteImage(s3_key, section, order)`, CMS API (GET/PATCH), Public API (no-auth), Tiptap editor в `/dashboard/cms/[section]/`, 11 секций, LeadForm с Turnstile, RBAC `cms.read/cms.write`, ADR-0023.

**Ключевые gaps:** meta пустое, нет Gallery UI (hooks есть), нет content history, нет live preview, нет валидации Tiptap JSON на backend, нет статусов Draft/Published.

## Topic Axes

- A1: Редактор контента — способы редактирования секций
- A2: Медиа и галерея — загрузка, хранение, упорядочивание
- A3: SEO и мета — title, description, OG tags, sitemap
- A4: Preview и публикация — live preview, staging, deploy flow
- A5: UX редактора в ERP — навигация, workflow пользователя

## Ranked Ideas

### 1. SEO Meta: структурированная схема + автодеривация + Sitemap
**Description:** Pydantic-схема `MetaSchema(title ≤60, description ≤160, og_image_s3_key, canonical, schema_org)`. При сохранении: автодеривация H1→title, первый параграф→description. Флаг `is_manual_override` защищает ручные правки. `/sitemap.xml` из `PUBLISHED` страниц, инвалидация через Outbox `PagePublished`. Ping Google при публикации.
**Axis:** A3
**Basis:** `direct:` `SitePageContent.meta` — пустое JSONField, gap зафиксирован в codebase. `direct:` django.contrib.sitemaps в стеке.
**Rationale:** Публичный сайт выходит в индекс с пустыми `<title>` и `<description>` — прямой SEO-ущерб сейчас. Compounding ROI: контент написан один раз, трафик идёт годами.
**Downsides:** Автодеривация может перезаписать ручные правки без флага `is_manual_override`.
**Confidence:** 95%
**Complexity:** Low
**Status:** Approved — Sprint #3

### 2. Draft / Published / Scheduled publishing
**Description:** Поля `status: DRAFT|PUBLISHED|SCHEDULED`, `publish_at: DateTimeField?`. Публичный API отдаёт только `PUBLISHED`. Celery beat каждые 5 мин: `publish_scheduled_pages()`. Outbox → `PagePublished` event. RBAC: `cms.write` → DRAFT, `cms.publish` → PUBLISHED.
**Axis:** A4
**Basis:** `direct:` Celery beat + `notifications` queue в CLAUDE.md. `reasoned:` сейчас сохранение = немедленная публикация — нет staging.
**Rationale:** Предотвращает случайную публикацию, позволяет готовить контент заблаговременно, атомарная смена кампаний.
**Downsides:** Нужна новая RBAC-роль `cms.publish` + миграция поля в `SitePageContent`.
**Confidence:** 90%
**Complexity:** Medium
**Status:** Approved — Sprint #3

### 3. Tiptap JSON Validation — Pydantic v2
**Description:** `TiptapDocSchema` — Pydantic v2 discriminated union: `{type: "doc", content: Block[]}`. PATCH endpoint принимает `content: TiptapDocSchema`, невалидный JSON → 422 с точным указанием пути. Предотвращает XSS через кастомные атрибуты, битые ссылки.
**Axis:** A1
**Basis:** `direct:` gap "нет валидации Tiptap JSON" зафиксирован. `direct:` Django Ninja + Pydantic v2 в стеке.
**Rationale:** Security hygiene: без валидации сломанный контент тихо уходит в БД и ломает публичный сайт при рендере. При обновлении Tiptap extensions нужно обновлять схему синхронно.
**Downsides:** При обновлении Tiptap extensions схему нужно обновлять синхронно.
**Confidence:** 93%
**Complexity:** Low
**Status:** Approved — Sprint #3

### 4. Gallery UI — drag-and-drop + bulk upload
**Description:** DnD-грид (@dnd-kit), bulk upload с прогрессом, inline alt-text, soft delete. PATCH `/api/v1/cms/images/reorder/` с `[{id, order}]`. Секция выбирается из 11 available. Tenant isolation обязательна.
**Axis:** A2
**Basis:** `direct:` `SiteImage(s3_key, section, order)` + `useCmsGallery()` hook — модель и API есть, UI нет.
**Rationale:** Единственный dead-code gap: SiteImage без UI = изображения невозможно загрузить из панели.
**Downsides:** @dnd-kit — новая зависимость. S3 presigned URL нужен.
**Confidence:** 92%
**Complexity:** Medium
**Status:** Approved — Sprint #4

### 5. Content Versioning — SitePageRevision
**Description:** `SitePageRevision(page_content_id, content_snapshot JSON, author_id, created_at)`. Создаётся при каждом сохранении. UI: последние 20 версий, preview, "Восстановить" (forward-only). Retention: лимит 20 версий per slug.
**Axis:** A4
**Basis:** `direct:` gap "нет content history". `direct:` Outbox + `transaction.on_commit()` в стеке.
**Rationale:** Убирает страх редактирования. Откат без разработчика.
**Downsides:** Рост объёма БД, нужна retention политика.
**Confidence:** 88%
**Complexity:** Low-Medium
**Status:** Approved — Sprint #4

### 6. ERP Object Showcase — live bridge
**Description:** Блок `objects_showcase` читает Объекты из ERP (фильтр: статус=активный, tenant). Редактор выбирает только `filter_preset`. Новый объект в ERP → автоматически на сайте.
**Axis:** A5
**Basis:** `reasoned:` ERP содержит Объекты с адресами, статусами. Public API `/api/v1/public/` уже изолирован. `direct:` `apps/gis/` геокодинг есть.
**Rationale:** Flywheel: LeadForm → Заявка → Объект → сайт → новые лиды. Нулевое обслуживание контента.
**Downsides:** Нужен аудит какие поля безопасно экспортировать (без PII).
**Confidence:** 85%
**Complexity:** Low-Medium
**Status:** Approved — Sprint #5

### 7. Pre-publish Gate + Section Completion Score
**Description:** CMS Dashboard показывает `completion_score` (0–100%) каждой секции. Кнопка "Опубликовать" заблокирована если score < 80%. Вычисляется на фронте.
**Axis:** A5
**Basis:** `reasoned:` gap "meta пустое" — симптом отсутствия принудительного контроля.
**Rationale:** Онбординг нетехнических пользователей без обучения.
**Downsides:** 80% threshold произвольный.
**Confidence:** 82%
**Complexity:** Low
**Status:** Deferred

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | Live Preview Redis draft | A4 дважды покрыт; Medium complexity; Sprint #4+ |
| 2 | Image Pipeline WebP | A2 покрыт Gallery; defer |
| 3 | Inline Edit на публичном сайте | Medium-High complexity; auth bridge; Sprint #4+ |
| 4 | SSG on Demand | Архитектурный mismatch с dynamic tenant data |
| 5 | Cross-Tenant Templates | Scope overrun |
| 6 | LLM SEO Auto-Pilot | Дублирует #1 без LLM; лишняя API-зависимость |
| 7 | Client Portal | Subject-replacement move |
| 8 | Per-section RBAC | Преждевременно при текущей команде |
| 9 | A/B Content Variants | Scope overrun Sprint #3 |
| 10 | Gallery as Linked List | Over-engineering при текущем scale |
| 11 | LeadForm Field Analytics | Out of scope Sprint #3 |
| 12 | Section Visibility Scheduling | Дублирует #2 |
| 13 | Client Inline Editor via link | Security complexity |
| 14 | Structured Field Editor | Лучше как brainstorm variant |
| 15 | Media Manifest | Дублирует Gallery |
| 16 | Client Portal (ERP iframe) | Дублирует Inline Edit |
