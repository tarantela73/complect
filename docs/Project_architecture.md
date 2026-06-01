# ТЕХНИЧЕСКАЯ СПЕЦИФИКАЦИЯ АРХИТЕКТУРЫ ERP «КОМПЛЕКТ»

---

Версия документа: 2.1.0 — ENTERPRISE GOVERNED EDITION
Статус: APPROVED FOR IMPLEMENTATION
Архитектурный стиль: Modular Monolith + CQRS Read Model + BFF + Next.js Feature Architecture
Регламент изменений: Любое архитектурное изменение оформляется через ADR в `docs/adr/`.

---

## 1. АРХИТЕКТУРНЫЙ МАНИФЕСТ

### ERP «КОМПЛЕКТ» проектируется как

- Высоконагруженная корпоративная ERP-платформа
- Событийно-ориентированный модульный монолит
- ASGI-only система реального времени
- Платформа с полным observability-контуром
- Система с enforced governance и SRE/SLA контролем

### Основные цели

- Отказоустойчивость
- Слабая связанность доменов
- Предсказуемая эволюция API
- Юридическая трассируемость действий
- Безопасность персональных данных
- Возможность масштабирования до multi-tenant SaaS

---

## 2. BOUNDED CONTEXTS (DDD)

### 2.1 Identity & Process Context

- Приложения: `apps/users/`, `apps/workflows/`
- Термины: `User`, `Role`, `Permission`, `MFAChallenge`, `Session`, `WorkflowState`
- Ответственность: MFA, RBAC, JWT rotation, Device Fingerprinting, BPM/FSM workflows

### 2.2 Contracts Context

- Приложения: `apps/contracts/`, `apps/export/`, `apps/reports/`, `apps/contragents/`
- Термины: `Contract`, `AdditionalAgreement`, `Act`, `ContractStatus`, `Person`, `Contragent`
- Ответственность: Жизненный цикл договоров, workflow закрытия, PDF документы, compliance exports, валидаторы контрагентов

### 2.3 Finance Context

- Приложения: `apps/finance/`, `apps/analytics/`, `apps/integration/`
- Термины: `Transaction`, `Invoice`, `PaidAmount`, `RemainingAmount`
- Ответственность: Ledger accounting, дебиторская задолженность, BFF analytics, интеграции с 1С

### 2.4 GIS Context

- Приложения: `apps/objects/`, `apps/search/`
- Ответственность: PostGIS, spatial indexes, GeoJSON, картографические сервисы
- Типы полей: `PointField` (координаты), `PolygonField` (границы объектов)
- Индексы: GIST spatial index обязателен на всех геополях
- API: GeoJSON FeatureCollection как стандарт ответа для пространственных запросов

### 2.5 Realtime Context

- Приложения: `apps/realtime/`, `apps/chat/`, `apps/notifications/`, `apps/outbox/`
- Ответственность: WebSockets, Presence, Event Replay, ACK delivery guarantees

---

## 3. СКВОЗНАЯ ТОПОЛОГИЯ СИСТЕМЫ И ПОТОКИ ДАННЫХ

```text
Next.js Front-end Layer (Папка: /frontend)
    ├── Public SSR (Маршруты: /, /about, /contacts -> Идеал для SEO Яндекса)
    ├── Private SPA (Маршрут: /dashboard/* -> Закрытая рабочая область)
    ├── TanStack Query (Слой Server State: автоматический кэш и фоновый рефреш)
    ├── Zustand (Слой Client UI State: сессии, модалки, WebSocket-клиент)
    └── Feature-Based UI Components (Изолированные модули-черные ящики)
                               │ ▲
                  HTTP (REST)  │ │ WebSockets (WSS)
                               ▼ │
    Nginx Edge Proxy & API Gateway (Папка: /infra)
    ├── Nginx Edge Proxy (SSL/TLS Let's Encrypt, раздача и кэширование статики)
    ├── API Gateway (Kong/APISIX -> Валидация JWT, Rate Limiting, трассировка)
    └── Security Headers (CSP, HSTS, X-Frame-Options: DENY, nosniff)
                               │
                               ▼
    Django Modular Monolith (Папка: /backend)
    ├── Ninja API (Асинхронный REST-движок на базе Pydantic DTO, версии /api/v1/)
    ├── Schemas Layer (Pydantic DTO входящей валидации ИНН/ФИО и маскирования Out)
    ├── Services Layer [Command Side] (Чистая бизнес-логика изменений и транзакций)
    ├── Queries/Selectors [Query Side] (CQRS: легковесные SQL-выборки, защита от N+1)
    ├── Internal SDK Layer (Полная развязка модулей через контракты в apps/common/)
    ├── Transactional Outbox (Запись событий в БД strictly в рамках бизнес-транзакции)
    ├── Outbox Dispatcher (Публикация событий в Redis строго transaction.on_commit)
    ├── RBAC & Security (Argon2 хэшер, TOTP MFA, Device Fingerprinting, сессионный IDS)
    ├── Notification Center (Мультиканальный асинхронный пушер: In-App, Email, Telegram)
    ├── BFF / Analytics Layer (Сборщик тяжелой аналитики дашборда в 1 комбо-запрос)
    └── File Antivirus Pipeline (Асинхронный шнур ClamAV: Quarantine -> Prod Bucket)
                                                                │
         ┌─────────────────────┴─────────────────────┐
         ▼                                           ▼
    Redis Layer                              PostgreSQL 16 + PostGIS
    ├── Cache (Агрегированные BFF-модели)    ├── Реляционная СУБД (Транзакции)
    ├── Pub/Sub (Шина Live-событий)          ├── Пространственные индексы GIST/GIN
    ├── Queue (Очередь задач Celery Only)    ├── PointField (Гео-координаты для карт)
    ├── Redis-based distributed locks        └── Optimistic Locking (Поле version)
    └── WebSocket State (Channels)                   │
         │                                           ▼
         ▼                                   S3 Storage Layer
    LiveKit Media SFU                        └── Приватные облачные бакеты
    └── WebRTC (Аудио/Видео созвоны)             (Доступ через Presigned URLs)
```

---

## 4. BACKEND ARCHITECTURE RULES

### 4.1 CQRS

- **Command Side** (`services/`): изменение состояния, DB transactions, Outbox write, orchestration. Heavy SELECTs запрещены.
- **Query Side** (`queries/`, `selectors/`): optimized reads, aggregation, DTO projection, pagination. Запись запрещена.

### 4.2 ASGI ONLY

- Продакшн работает исключительно через: Granian, Uvicorn, Daphne.
- WSGI в production **запрещён**.

### 4.3 TRANSACTION BOUNDARY LAW

- События публикуются строго: `DB COMMIT → transaction.on_commit() → Redis publish / Celery dispatch`.
- Запрещено публиковать события до коммита транзакции.

### 4.4 INTERNAL SDK LAYER

- Кросс-импорты моделей между доменами **запрещены** (ADR-0005).
- Общение модулей идёт строго через `apps/common/contracts/` (typing.Protocol).

### 4.5 DOMAIN EVENTS CONTRACT

- Все события: `@dataclass(slots=True, frozen=True)`
- Обязательные поля: `event_id`, `event_version`, `occurred_at`, `correlation_id`

### 4.6 BASE ENTERPRISE MODEL

Каждая доменная модель **обязана** наследовать `BaseEnterpriseModel`:

```python
class BaseEnterpriseModel(models.Model):
    id: UUID          # Primary key
    created_at        # auto
    updated_at        # auto
    deleted_at        # soft delete timestamp
    is_deleted: bool  # soft delete flag
    version: int      # optimistic concurrency version counter
    tenant_id: UUID   # tenant isolation
    is_legal_hold: bool  # legal hold flag
```

- `save()` / `delete()` в методах модели **запрещены** — мутации только через `EnterpriseSreRepository`.
- Soft delete реализуется через `is_deleted=True` + `deleted_at=now()`.

### 4.7 OPTIMISTIC CONCURRENCY CONTROL

- Все state mutations идут через `EnterpriseSreRepository.atomic_cas_update()` — никогда через raw `.save()`.
- Finance mutations: `SELECT ... FOR UPDATE` (row-level lock).
- Critical balance recompute: `SERIALIZABLE` + advisory locks.

### 4.8 LEGAL HOLD POLICY

- Флаг `is_legal_hold=True` **блокирует**: soft delete, hard delete, mutation, retention cleanup.
- Проверка обязательна перед любой мутацией объекта.

### 4.9 DATABASE CONVENTIONS

- Naming: `snake_case`, plural table names (пример: `contracts_additional_agreements`).
- Default isolation: `READ COMMITTED`.

### 4.10 MIGRATION POLICY — Expand & Contract (ADR-0002)

1. **Release N**: добавить nullable column.
2. **Release N+1**: async Celery backfill (батчи 5000 через `SKIP LOCKED`), переключить write на новый столбец.
3. **Release N+2**: `NOT NULL` constraint (`NOT VALID` → `VALIDATE CONSTRAINT`), удалить старый столбец.

Запрещено:

- `AddField(NOT NULL, no default)`
- `RemoveField` на активных столбцах
- Индексы без `CONCURRENTLY`
- Миграции без `reverse_sql` / `backwards()`

### 4.11 CURSOR PAGINATION STANDARD

- `OFFSET` pagination **запрещена** для: audit logs, finance, notifications, websocket events, chats.
- Стандарт: `WHERE id < cursor ORDER BY id DESC`.

### 4.12 READ REPLICA STRATEGY

- Query Side: читает из replica.
- Command Side: пишет только в primary.

### 4.13 READ-YOUR-OWN-WRITES POLICY

- После mutation чтение идёт из primary.
- TTL sticky routing: 2 секунды.

### 4.14 TENANT ENFORCEMENT LAW

- Все QuerySet **обязаны** проходить через tenant-aware manager.
- Raw ORM без tenant scope **запрещён**.
- Фильтр применяется автоматически через `TenantManager.get_queryset()`.

### 4.15 CELERY QUEUE TOPOLOGY

| Очередь | Назначение | Priority |
| --- | --- | --- |
| `default` | Общие задачи | normal |
| `outbox` | Диспетчер Transactional Outbox | high |
| `notifications` | Email, Telegram, In-App пуши | normal |
| `finance` | Финансовые пересчёты, выписки | high |
| `files` | ClamAV pipeline, S3 upload | low |
| `retention` | Очистка по retention policy | low |

- Prefetch multiplier: 1 (CELERY_WORKER_PREFETCH_MULTIPLIER=1).
- Acks late: true (задача подтверждается после выполнения).
- Max retries: 5, после — эвакуация в DLQ.

---

## 5. SECURITY & COMPLIANCE

### 5.1 AUTHENTICATION

- Пароли: Argon2, 12+ chars, uppercase/lowercase, digit, special char.
- MFA: TOTP, backup codes, Redis TTL 5m.
- Tokens: Access Token (in-memory only), Refresh Token (HttpOnly, Secure, SameSite=Strict).

### 5.2 DEVICE FINGERPRINTING

- При изменении fingerprint: revoke session, emit security event, notify chief.

### 5.3 IDEMPOTENCY POLICY

- Mutations требуют заголовок `Idempotency-Key: UUID`.
- Redis TTL для ключей идемпотентности: 24h.

### 5.4 FILE SECURITY PIPELINE

- FSM pipeline: `uploaded → quarantine → virus_scanning → processing → ready → failed`.

### 5.5 ANTIVIRUS PIPELINE

- ClamAV daemon, quarantine bucket, DLQ on failure.

### 5.6 LEGAL HOLD POLICY

- Флаг `is_legal_hold=True` блокирует: soft delete, hard delete, mutation, retention cleanup.
- Проверка обязательна до любой мутации — см. 4.8.

### 5.7 PII ENCRYPTION

- Алгоритм: AES-256 via pgcrypto (`pgp_sym_encrypt` / `pgp_sym_decrypt`).
- Шифруются поля: `passport`, `snils`, `phone`.
- Ключ шифрования: из Docker Secret / Vault, никогда в коде или env.

### 5.8 PII ACCESS AUDIT

- Каждое чтение PII логируется: `who`, `when`, `why`, `request_id`, `correlation_id`.

### 5.9 SECRETS ROTATION POLICY

- JWT: 90 дней, DB: 30 дней, S3: 60 дней.

### 5.10 CLOCK SYNCHRONIZATION

- UTC everywhere. NTP sync mandatory.
- Drift policy: Warning >2s; Critical >5s; Block finance >15s.

---

## 6. API GOVERNANCE

### 6.1 VERSIONING

- Базовый префикс: `/api/v1/`

### 6.2 LIFECYCLE

- Мажорная версия v1 поддерживается до: 2028-01-01.

### 6.3 DEPRECATION HEADERS

- Заголовки `Deprecation:` и `Sunset:` обязательны для устаревших endpoint'ов.

### 6.4 OPENAPI FREEZE POLICY (ADR-0003)

- Спецификация OpenAPI = контракт. Breaking changes требуют: ADR, новую major версию, migration window.

### 6.5 GENERATED SDK

- Путь TS SDK: `frontend/src/shared/api/generated/`. Ручные правки запрещены.

### 6.6 API ERROR RESPONSE FORMAT

Все ошибки возвращаются в едином формате:

```json
{
  "error": {
    "code": "CONTRACT_NOT_FOUND",
    "message": "Договор не найден в контуре текущей компании.",
    "detail": null,
    "request_id": "uuid",
    "correlation_id": "uuid"
  }
}
```

| HTTP статус | Семантика |
| --- | --- |
| 400 | Ошибка валидации входных данных |
| 401 | Не аутентифицирован |
| 403 | Нет прав (RBAC / Legal Hold / Tenant) |
| 404 | Объект не найден в scope тенанта |
| 409 | Конфликт версии (optimistic lock) |
| 422 | Бизнес-правило нарушено |
| 429 | Rate limit exceeded |
| 503 | Kill-switch / readonly mode |

- `code` — машиночитаемый enum в SCREAMING_SNAKE_CASE.
- `detail` — опциональный объект с полями валидации (для 400/422).

---

## 7. FRONTEND ARCHITECTURE

### 7.1 STACK

- Next.js 15+, React 19, Turbopack, TypeScript.

### 7.2 STATE MANAGEMENT

- Server State: TanStack Query only.
- UI State: Zustand only.

### 7.3 FEATURE ISOLATION

- Папка модулей: `/features/*`. Feature modules are black boxes.

### 7.4 SERVER COMPONENTS FIRST

- По умолчанию Server Components. `"use client"` разрешён только для interactive leaves.

### 7.5 ERROR BOUNDARY STRATEGY

- `error.tsx` обязателен в каждой папке роута.
- Query Retry: ровно 3 ретрая перед fallback.
- Тяжёлые виджеты: `<Suspense fallback={<Skeleton />}>`.

### 7.6 ACCESSIBILITY (WCAG 2.1 AA)

- Mandatory: semantic tables, keyboard navigation, focus-visible, aria-live alerts, accessible dialogs.

### 7.7 DESIGN TOKEN POLICY

- Запрещены hardcoded значения для colors, spacing, z-index, typography. Только centralized design tokens.

---

## 8. REALTIME & WEBSOCKETS

### 8.1 PROTOCOL HANDSHAKE

```json
{
  "type": "connection_init",
  "protocol_version": 1,
  "capabilities": ["at_least_once", "event_replay"]
}
```

### 8.2 ACK POLICY

- Каждое событие требует ACK. Timeout ожидания: 3s.

### 8.3 EVENT REPLAY

- Пропущенные события replayed из Redis queue.

### 8.4 EVENT ORDERING POLICY

- События содержат `sequence_id`. Frontend игнорирует stale события.

### 8.5 RECONNECT POLICY

- Exponential backoff + jitter + replay sync.

---

## 9. FEATURE FLAGS GOVERNANCE

- Допустимые типы флагов: `temporary`, `permanent`, `experiment`, `ops`, `kill-switch`.

### 9.1 CLEANUP LAW

- Для типов `temporary` / `experiment`: max age = 90 дней.

---

## 10. OBSERVABILITY & SRE

### 10.1 STACK

- OpenTelemetry, Jaeger, Prometheus, Grafana, structlog.

### 10.2 LOGGING CONTRACT

Обязательные поля каждого JSON-лога:

```json
{
  "timestamp": "ISO8601",
  "level": "INFO|WARNING|ERROR|CRITICAL",
  "request_id": "uuid",
  "correlation_id": "uuid",
  "tenant_id": "uuid",
  "user_id": "uuid",
  "event_id": "uuid",
  "module": "apps.contracts.services"
}
```

### 10.3 ERROR BUDGET POLICY (ADR-0004)

- SLA (скользящее 30-дневное окно): REST API — 99.9%, WebSocket — 99.5%, Dashboard — 99.0%.

### 10.4 BUDGET EXHAUSTED LAW

- Error Budget = 0% → feature freeze + reliability mode only.

---

## 11. RELIABILITY & INTEGRATIONS

### 11.1 DEAD LETTER QUEUE

- Retry limit Celery: 5. После — эвакуация в Redis-стрим `infrastructure:dlq:stream`.

### 11.2 ADMIN DLQ PANEL

- Доступные операции: inspect, replay, purge.

### 11.3 ANTI-CORRUPTION LAYER

- Структура: `adapters/`, `mappers/`, `anti_corruption/`.

### 11.4 INBOX PATTERN

- External events сохраняются в `integration_inbox`.
- Дедупликация по ключу `external_event_id`.

---

## 12. SEARCH ENGINE

- Только Postgres FTS. ElasticSearch/OpenSearch не используется.
- GIN indexes, russian morphology (стемминг через `pg_catalog.russian`), `ts_rank_cd`.
- Веса ранжирования:
  - Weight A: ИНН, номера
  - Weight B: названия, ФИО
  - Weight C: адреса
  - Weight D: OCR-текст

---

## 13. AUDIT INTEGRITY CHAIN

- Каждая запись audit log: `SHA256(previous_hash + payload + timestamp)`.
- Tampering detection → немедленный kill-switch финансового контура + critical alert.

---

## 14. PERFORMANCE & CACHE

### 14.1 CACHE STAMPEDE PROTECTION

- Тяжёлая инвалидация: distributed lock + stale-while-revalidate.

### 14.2 SLO TARGETS

| Endpoint | p95 | p99 |
| --- | --- | --- |
| REST API | < 150ms | < 300ms |
| Dashboard BFF | < 500ms | — |
| Search | < 200ms | — |

---

## 15. TESTING STRATEGY

### 15.1 TEST MATRIX

- pytest + testcontainers-python (PostgreSQL + PostGIS, Redis).
- SQLite в тестах **категорически запрещён**.

### 15.2 TEST SUITES

| Папка | Ответственность |
| --- | --- |
| `unit/` | Edge cases в Service layer |
| `queries/` | `assert_num_queries`, replica routing, pagination |
| `permissions/` | RBAC policies end-to-end |
| `contract/` | OpenAPI snapshots, DTO compatibility |
| `websocket/` | ACK, replay, reconnect, negotiation |
| `migrations/` | Forward/backward compatibility |
| `performance/` | k6, Locust stress tests |
| `security/` | MFA bypass, JWT rotation, IDOR, privilege escalation |

### 15.3 MUTATION TESTING

- Finance domain: `mutmut >= 90%`.

### 15.4 PLAYWRIGHT E2E

- Flows: login, MFA, dashboard, contract close, websocket, finance, maps.

---

## 16. DEFINITION OF DONE

Merge заблокирован CI/CD если:

- Упал хотя бы один тест из матрицы
- Code coverage ниже порогов (см. раздел 17)
- Mutation score ниже целевой
- Обнаружена деструктивная миграция (нарушение Expand & Contract)
- Отсутствует ADR для архитектурного изменения
- Generated TS SDK устарел (изменились схемы бэкенда)

---

## 17. COVERAGE THRESHOLDS

| Слой | Минимум |
| --- | --- |
| Services | ≥ 90% |
| Repositories | ≥ 85% |
| RBAC | ≥ 95% |
| API | ≥ 80% |
| Frontend hooks | ≥ 80% |

---

## 18. MONOREPO STRUCTURE (ЭТАЛОННОЕ АРХИТЕКТУРНОЕ ДЕРЕВО)

```text
Complect/                          # Корень монорепозитория
├── .gitignore
├── ruff.toml                      # Глобальные правила линтинга Python
├── docker-compose.dev.yml         # Локальное окружение с Hot-Reload
├── docker-compose.prod.yml        # Enterprise-сборка (Docker Secrets)
│
├── docs/
│   └── adr/                       # ARCHITECTURE DECISION RECORDS
│       ├── TEMPLATE.md
│       ├── 0001-database-partitioning-strategy.md
│       ├── 0002-zero-downtime-migration-governance.md
│       ├── 0003-openapi-schema-freeze.md
│       ├── 0004-sre-slo-governance.md
│       ├── 0005-bounded-context-ownership.md
│       ├── 0006-jwt-auth-token-rotation.md
│       ├── 0007-api-gateway-trust-boundary.md
│       ├── 0008-cursor-pagination-enforcement.md
│       └── 0009-pii-vault-query-masking.md
│
├── infra/
│   ├── nginx/                     # SSL, CSP, HSTS заголовки
│   ├── api_gateway/               # Kong / APISIX конфигурация
│   ├── opentelemetry/             # OTel Collector → Jaeger
│   ├── performance/               # k6, Locust скрипты
│   ├── prometheus/
│   └── grafana/
│
├── backend/
│   ├── manage.py
│   ├── ruff.toml
│   ├── config/
│   │   ├── celery.py
│   │   ├── asgi.py                # Единая точка входа ASGI (REST + WS)
│   │   ├── urls.py
│   │   └── settings/
│   │       ├── base.py
│   │       ├── local.py
│   │       ├── stage.py
│   │       └── prod.py
│   └── apps/
│       ├── common/                # Инфраструктура: Outbox, tenancy, events, security
│       │   ├── contracts/         # Internal SDK (typing.Protocol контракты)
│       │   ├── events/            # @dataclass(slots=True, frozen=True) события
│       │   ├── logging/           # structlog, X-Correlation-ID propagation
│       │   └── repository/        # EnterpriseSreRepository, atomic_cas_update
│       ├── users/                 # Identity: Auth, RBAC, MFA, JWT, сессии
│       ├── contragents/           # Person, Contragent, валидаторы ИНН/ОГРН
│       ├── contracts/             # Договоры, ДС, Акты (services/, queries/)
│       ├── finance/               # Транзакции, платежи, баланс дебиторки
│       ├── objects/               # GIS: PostGIS PointField, spatial queries
│       ├── realtime/              # ASGI WebSocket Consumers, Presence, LiveKit
│       └── integration/           # 1С ACL, Inbox pattern, mappers
│
└── frontend/
    ├── package.json
    ├── next.config.js
    └── src/
        ├── middleware.ts           # Защитник /dashboard
        ├── app/
        │   ├── page.tsx            # Публичный лендинг
        │   └── (dashboard)/        # Зона дашборда
        ├── features/               # Функциональные модули (чёрные ящики)
        │   ├── auth/
        │   ├── contracts/
        │   ├── finance/
        │   ├── map/
        │   └── realtime/
        └── shared/
            └── api/
                └── generated/      # TS SDK (ручные правки запрещены)
    └── e2e/
        ├── playwright.config.ts
        └── specs/
```

---

## 19. FINAL ENGINEERING LAW

- **Explicit over implicit** — явное лучше неявного
- **Reliability over velocity** — надёжность важнее скорости разработки
- **Contracts over assumptions** — жёсткие контракты важнее предположений
- **Observability first** — мониторинг закладывается на этапе проектирования
- **Async by default** — асинхронность по умолчанию в тяжёлых операциях
- **Governance enforced by CI/CD** — все архитектурные правила проверяются машиной
- **Architecture evolves only through ADR** — архитектура развивается исключительно через документирование в журнале решений
