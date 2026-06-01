
# ТЕХНИЧЕСКАЯ СПЕЦИФИКАЦИЯ АРХИТЕКТУРЫ ERP «КОМПЛЕКТ»

---
---

Версия документа: 2.0.0 — ENTERPRISE GOVERNED EDITION
Статус: APPROVED FOR IMPLEMENTATION
Архитектурный стиль: Modular Monolith + CQRS Read Model + BFF + Next.js Feature Architecture
Регламент изменений: Любое архитектурное изменение оформляется через ADR в docs/adr/.

## 1. АРХИТЕКТУРНЫЙ МАНИФЕСТ

---

#### ERP «КОМПЛЕКТ» проектируется как

- Высоконагруженная корпоративная ERP-платформа;
- Событийно-ориентированный модульный монолит;
- ASGI-only система реального времени;
- Платформа с полным observability-контуром;
- Система с enforced governance и SRE/SLA контролем.

#### Основные цели

- Отказоустойчивость;
- Слабая связанность доменов;
- Предсказуемая эволюция API;
- Юридическая трассируемость действий;
- Безопасность персональных данных;
- Возможность масштабирования до multi-tenant SaaS.

## 2. BOUNDED CONTEXTS (DDD)

### 2.1 Identity & Process Context

- Приложения: apps/users/, apps/workflows/
- Термины: `User`, `Role`, `Permission`, `MFAChallenge`, `Session`, `WorkflowState`.
- Ответственность: `MFA`, `RBAC`, `JWT rotation`, `Device Fingerprinting`, `BPM/FSM workflows`.

### 2.2 Contracts Context

- Приложения: apps/contracts/, apps/export/, apps/reports/, apps/contragents/
- Термины: Contract, AdditionalAgreement, Act, ContractStatus, Person, Contragent.
- Ответственность: Жизненный цикл договоров, workflow закрытия, PDF документы, compliance exports, валидаторы контрагентов

### 2.3 Finance Context

- Приложения: apps/finance/, apps/analytics/, apps/integration/
- Термины: Transaction, Invoice, PaidAmount, RemainingAmount
- Ответственность: Ledger accounting, дебиторская задолженность, BFF analytics, интеграции с 1С

### 2.4 GIS Context

- Приложения: apps/objects/, apps/search/
- Ответственность: PostGIS, spatial indexes, GeoJSON, картографические сервисы

### 2.5 Realtime Context

- Приложения: apps/realtime/, apps/chat/, apps/notifications/, apps/outbox/
- Ответственность: WebSockets, Presence, Event Replay, ACK delivery guarantees

## 3. СКВОЗНАЯ ТОПОЛОГИЯ СИСТЕМЫ И ПОТОКИ ДАННЫХ

---

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
    ├── Pub/Sub (Шина Live-событий)          ├── Пространственные индексы R-Tree
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

- Command Side:
  - Папка: services/
  - Разрешено: Изменение состояния, DB transactions, Outbox write, orchestration.
  - Запрещено: Heavy SELECTs, аналитика, бизнес-логика в models.py.
- Query Side:
  - Папка: queries/, selectors/
  - Ответственность: Optimized reads, aggregation, DTO projection, pagination.

### 4.2 ASGI ONLY

- Продакшн полностью работает через: Granian, Uvicorn, Daphne.
- WSGI в production запрещен.

### 4.3 TRANSACTION BOUNDARY LAW

- События публикуются строго: DB COMMIT -> transaction.on_commit() -> Redis publish / Celery dispatch.
- Это исключает: Phantom events, stale reads, race conditions.

### 4.4 INTERNAL SDK LAYER

- Кросс-импорты моделей запрещены.
- Общение модулей идет строго через: apps/common/contracts/

### 4.5 DOMAIN EVENTS CONTRACT

- Все события описываются как: @dataclass(slots=True, frozen=True)
- Обязательные поля: event_id, event_version, occurred_at, correlation_id

### 4.6 DATABASE CONVENTIONS

- Naming: snake_case only, plural table names (Пример: contracts_additional_agreements).
- BaseEnterpriseModel: Каждая таблица наследует поля id, created_at, updated_at, deleted_at, is_deleted, version, tenant_id.

### 4.7 TRANSACTION ISOLATION POLICY

- Default: READ COMMITTED.
- Finance Mutations: SELECT ... FOR UPDATE.
- Critical Balance Recompute: SERIALIZABLE, advisory locks.

### 4.8 MIGRATION POLICY

- Strict Expand & Contract:
- 1. Add nullable field,
- 1. Async backfill,
- 1. Switch code,
- 1. Remove old field через 2 релиза.
- Destructive migrations forbidden.

### 4.9 CURSOR PAGINATION STANDARD

- OFFSET pagination запрещена для: audit logs, finance, notifications, websocket events, chats.
- Используется: WHERE id < cursor ORDER BY id DESC.

### 4.10 READ REPLICA STRATEGY

- Query Side: Читает из replica.
- Command Side: Пишет только в primary.

### 4.11 READ-YOUR-OWN-WRITES POLICY

- После mutation чтение идет из primary.
- TTL sticky routing: 2 seconds.

### 4.12 TENANT ENFORCEMENT LAW

- Все QuerySet обязаны проходить через tenant-aware manager.
- Raw ORM без tenant scope запрещен.

## 5. SECURITY & COMPLIANCE

### 5.1 AUTHENTICATION

- Пароли: Argon2, 12+ chars, uppercase/lowercase, digit, special char.
- MFA: TOTP, backup codes, Redis TTL 5m.
- Tokens: Access Token (in-memory only), Refresh Token (HttpOnly, Secure, SameSite=Strict).

### 5.2 DEVICE FINGERPRINTING

- При изменении fingerprint: Revoke session, emit security event, notify chief.

### 5.3 IDEMPOTENCY POLICY

- Mutations требуют заголовок Idempotency-Key: UUID.
- Redis TTL для ключей идемпотентности: 24h.

### 5.4 FILE SECURITY PIPELINE

- FSM pipeline: uploaded → quarantine → virus_scanning → processing → ready → failed.

### 5.5 ANTIVIRUS PIPELINE

- ClamAV daemon, quarantine bucket, DLQ on failure.

### 5.6 LEGAL HOLD POLICY

- Флаг is_legal_hold = True блокирует: soft delete, hard delete, mutation, retention cleanup.

### 5.7 PII ENCRYPTION

- Алгоритм AES-256 via pgcrypto.
- Шифруются поля: passport, SNILS, phones.

### 5.8 PII ACCESS AUDIT

- Каждое чтение PII логируется: who, when, why, request_id, correlation_id.

### 5.9 SECRETS ROTATION POLICY

- Сроки плановой ротации: JWT — 90 days, DB — 30 days, S3 — 60 days.

### 5.10 CLOCK SYNCHRONIZATION

- UTC everywhere. NTP sync mandatory.
- Drift policy: Warning: >2s; Critical: >5s; Block finance: >15s.

## 6. API GOVERNANCE

### 6.1 VERSIONING

- Базовый префикс путей: /api/v1/

### 6.2 LIFECYCLE

- Мажорная версия v1 поддерживается строго до: 2028-01-01.

### 6.3 DEPRECATION HEADERS

- Заголовки Deprecation: и Sunset: являются mandatory для устаревших ручек.

### 6.4 OPENAPI FREEZE POLICY

- Спецификация OpenAPI = контракт. Breaking changes require: ADR, new major version, migration window.

### 6.5 GENERATED SDK

- Путь генерации TS SDK: frontend/src/shared/api/generated/. Manual edits forbidden.

## 7. FRONTEND ARCHITECTURE

### 7.1 NEXT.JS 15+

- Stack: Next.js 15+, React 19, Turbopack, TypeScript.

### 7.2 STATE MANAGEMENT

- Server State: TanStack Query only.
- UI State: Zustand only.

### 7.3 FEATURE ISOLATION

- Папка модулей: /features/*. Feature modules are black boxes.

### 7.4 SERVER COMPONENTS FIRST

- По умолчанию используются Server Components. Директива "use client" разрешена только для interactive leaves.

### 7.5 ERROR BOUNDARY STRATEGY

- Route Error Boundaries: Файл error.tsx mandatory в каждой папке роута.
- Query Retry: Ровно 3 ретрая перед срабатыванием fallback.
- Suspense: Тяжелые виджеты обязаны использовать структуру ```python <Suspense fallback={<Skeleton />}>```.

7.6 ACCESSIBILITY (WCAG 2.1 AA)

- Mandatory: Semantic tables, keyboard navigation, focus-visible, aria-live alerts, accessible dialogs.

7.7 DESIGN TOKEN POLICY

- Запрещены hardcoded значения для colors, spacing, z-index, typography. Все настраивается через centralized design tokens.

1. REALTIME & WEBSOCKETS
8.1 PROTOCOL HANDSHAKE

- При подключении Client sends кадр:
  {
    "type": "connection_init",
    "protocol_version": 1,
    "capabilities": ["at_least_once", "event_replay"]
  }

8.2 ACK POLICY

- Every event requires ACK. Timeout ожидания квитанции: 3s.

8.3 EVENT REPLAY

- Missed events replayed from Redis queue.

8.4 EVENT ORDERING POLICY

- Events contain: sequence_id. Frontend игнорирует старые (stale) события.

8.5 RECONNECT POLICY

- Алгоритмы: Exponential backoff, jitter, replay sync.

   1. FEATURE FLAGS GOVERNANCE

- Допустимые типы флагов: temporary, permanent, experiment, ops, kill-switch.

9.1 CLEANUP LAW

- Для флагов типов temporary/experiment устанавливается max age = 90 days.

   1. OBSERVABILITY & SRE
   10.1 STACK

- Инструменты: OpenTelemetry, Jaeger, Prometheus, Grafana, structlog.

10.2 LOGGING CONTRACT

- Mandatory fields для каждого JSON-лога: timestamp, level, request_id, correlation_id, tenant_id, user_id, event_id, module.

10.3 ERROR BUDGET POLICY

- Скользящее 30-дневное окно доступности (SLA): REST API — 99.9%, WebSocket — 99.5%, Dashboard — 99.0%.

10.4 BUDGET EXHAUSTED LAW

- Если Бюджет ошибок (Error Budget) = 0%: Активируются feature freeze и reliability mode only.

   1. RELIABILITY & INTEGRATIONS
   11.1 DEAD LETTER QUEUE

- Retry limit для Celery-задач: 5. After exhaustion задача эвакуируется в Redis-лист dead_letter_queue.

11.2 ADMIN DLQ PANEL

- Доступные операции в интерфейсе: inspect, replay, purge.

11.3 ANTI-CORRUPTION LAYER

- Структура интеграционных модулей: adapters/, mappers/, anti_corruption/.

11.4 INBOX PATTERN

- External events persisted in: integration_inbox. Дедупликация входящих сообщений по ключу external_event_id.

   1. SEARCH ENGINE

- Используется исключительно Postgres FTS.
- Технические средства: GIN indexes, russian morphology (стемминг), ts_rank_cd.
- Ранжирование по весам:
- Weight A: INN, Numbers
  - Weight B: Names
  - Weight C: Address
  - Weight D: OCR text

   1. AUDIT INTEGRITY CHAIN

- Каждая запись audit log содержит хэш: SHA256(previous_hash + payload + timestamp).
- Обнаружение несанкционированных изменений (Tampering) немедленно триггерит kill-switch финансового контура и отправляет critical alert.

   1. PERFORMANCE & CACHE
   14.1 CACHE STAMPEDE PROTECTION

- Тяжелая инвалидация и пересчет кэша защищены с помощью distributed lock и стратегии stale-while-revalidate.

14.2 SLO TARGETS

- Лимиты производительности: REST — p95 < 150ms, p99 < 300ms; Dashboard — p95 < 500ms; Search — p95 < 200ms.

   1. TESTING STRATEGY
   15.1 TEST MATRIX

- Инструменты окружения: pytest, testcontainers-python (PostgreSQL + PostGIS, Redis). SQLite в тестах категорически forbidden.

15.2 TEST SUITES

- unit/: Проверка крайних случаев (edge cases) в Service layer.
- queries/: Контроль количества запросов через assert_num_queries, проверка replica routing и корректности пагинации.
- permissions/: Сквозное тестирование политик RBAC.
- contract/: Проверка OpenAPI snapshots, DTO compatibility, выявление schema diff.
- websocket/: Эмуляция ACK квитанций, сценариев replay, reconnect и сокет-negotiation.
- migrations/: Валидация forward/backward compatibility миграций данных.
- performance/: Стресс-тестирование сценариев через k6 и Locust.
- security/: Тестирование устойчивости к MFA bypass, проверка JWT rotation, IDOR атак, privilege escalation и попыток audit tampering.

15.3 MUTATION TESTING

- Покрытие мутационными тестами для критичного Finance domain: mutmut >= 90%.

15.4 PLAYWRIGHT E2E

- Основные проверяемые пользовательские потоки (Flows): login, MFA, dashboard, contract close, websocket, finance, maps.

   1. DEFINITION OF DONE
   Слияние веток (Merge) полностью заблокировано на уровне CI/CD автоматизации, если:

- Any test fails (упал хотя бы один тест из матрицы);
- Code coverage drops below thresholds (упало покрытие кода);
- Mutation score fails (метрика mutmut ниже целевой);
- Unsafe migration detected (выявлена деструктивная миграция в обход Expand & Contract);
- ADR missing (отсутствует файл описания архитектурных изменений);
- Generated SDK outdated (изменились схемы бэкенда, но клиентский TS SDK в репозитории не обновлен).

   1. COVERAGE THRESHOLDS
   Целевые лимиты тестового покрытия по архитектурным слоям системы:

- Services: >= 90%
- Repositories: >= 85%
- RBAC: >= 95%
- API: >= 80%
- Frontend hooks: >= 80%

   1. MONOREPO STRUCTURE (ЭТАЛОННОЕ АРХИТЕКТУРНОЕ ДЕРЕВО)
   Complect/ # Корень монорепозитория
   ├── .gitignore # Скрывает .venv, node_modules, .env, .next
   ├── ruff.toml # Глобальные правила линтинга Python
   ├── docker-compose.dev.yml # Сборка локального окружения с Hot-Reload
   ├── docker-compose.prod.yml # Боевая Enterprise-сборка (Docker Secrets)
   │
   ├── docs/ # СЛОЙ СИСТЕМНОЙ ДОКУМЕНТАЦИИ
   │ └── adr/ # ARCHITECTURE DECISION RECORDS (ADR)
   │ ├── 0001-modular-monolith.md
   │ ├── 0002-cqrs-selectors.md
   │ ├── 0003-transactional-outbox.md
   │ └── 0004-api-error-standard.md
   │
   ├── infra/ # СЛОЙ ИНФРАСТРУКТУРЫ
   │ ├── docker/ # Backend/Frontend Dockerfiles (Dev/Prod)
   │ ├── nginx/ # nginx.conf (SSL, CSP, HSTS заголовки)
   │ ├── api_gateway/ # Конфигурация Kong / APISIX шлюза
   │ ├── opentelemetry/ # Конфиг OTel Collector для пересылки в Jaeger
   │ ├── performance/ # Скрипты нагрузочного теста (k6, Locust)
   │ ├── prometheus/ # prometheus.yml Сборщик метрик
   │ └── grafana/ # Provisioning дашбордов Grafana
   │
   ├── backend/ # СЛОЙ БЭКЕНДА (Django Monolith)
   │ ├── manage.py
   │ ├── pyproject.toml # Зависимости Python (управление через Uv)
   │ ├── .coveragerc # Настройка слоеных порогов покрытия
   │ ├── config/ # Конфигурация ядра Django
   │ │ ├── init.py # Тут Celery инициализируется при старте ASGI
   │ │ ├── celery.py # Корневая конфигурация Селери (Очереди, Beat)
   │ │ ├── asgi.py # ЕДИНАЯ ТОЧКА ВХОДА АСИНХРОННОГО ПРИЛОЖЕНИЯ (REST + WS)
   │ │ ├── urls.py # Роутер URL для локальной админки
   │ │ └── settings/ # Environment Stratification Настройки
   │ │ ├── init.py
   │ │ ├── base.py # Общие настройки приложений и middlewares
   │ │ ├── local.py # Настройки разработки (DEBUG=True)
   │ │ ├── stage.py # Настройки стейджинга (Песочница)
   │ │ └── prod.py # Боевые настройки (DEBUG=False, S3, Secrets)
   │ └── apps/ # ДОМЕННЫЕ ПРИЛОЖЕНИЯ БЭКЕНДА
   │ ├── common/ # Миксины, Soft Delete, Antivirus Pipeline, Locks, Идемпотентность
   │ │ ├── contracts/ # INTERNAL SDK (Абстрактные контракты модулей)
   │ │ ├── logging/ # Контекст и процессоры structlog для проброса X-Correlation-ID
   │ │ └── events/ # СЛОЙ СТРОГО ТИПИЗИРОВАННЫХ СОБЫТИЙ (@dataclass(slots=True))
   │ ├── users/ # Identity Context: Auth, User, Роли RBAC, MFA, Сессии
   │ ├── contragents/ # Contracts Context: Сущности Person, Contragent, Валидаторы
   │ ├── contracts/ # Contracts Context: Договоры, ДС, Акты (Слои: services/, queries/)
   │ ├── finance/ # Finance Context: Транзакции, Платежи, логика Баланса дебиторки
   │ ├── objects/ # GIS Context: Логика объектов (PostGIS PointField)
   │ ├── realtime/ # Realtime Context: ASGI WebSocket Consumers, Presence, LiveKit
   │ └── ... # Остальные доменные модули (outbox, search, workflows, etc.)
   │
   └── frontend/ # СЛОЙ ФРОНТЕНДА (Next.js Application)
   ├── package.json # Зависимости (Next, TanStack, Zustand)
   ├── next.config.js # Настройки сборщика Turbopack (SWC)
   ├── src/
   │ ├── middleware.ts # Защитник /dashboard (Next.js Middleware)
   │ ├── app/ # Файловый роутинг (Только импорт Фич!)
   │ │ ├── page.tsx # Публичный Лендинг (Главная)
   │ │ └── (dashboard)/ # Зона дашборда с 3-х панельным Layout
   │ └── features/ # ФУНКЦИОНАЛЬНО-МОДУЛЬНЫЕ ФИЧИ (Черные ящики)
   │ ├── auth/ # Вход, Zustand authStore, Слой Feature Flags
   │ ├── contracts/ # Таблицы договоров, TanStack hooks, компоненты ДС, Service Adapters
   │ ├── finance/ # Финансовые графики Recharts для Шефа
   │ ├── map/ # Интерактивная карта Яндекс/2ГИС
   │ └── realtime/ # Глобальный сокет-клиент (ACK / Reconnect Policy)
   └── e2e/ # СЛОЙ СКВОЗНЫХ E2E ТЕСТОВ (Playwright Matrix)
   ├── playwright.config.ts
   └── specs/ # Спеки: login, mfa, dashboard, contract close, map
   2. FINAL ENGINEERING LAW
   Система проектируется строго по нерушимым принципам:

- Explicit over implicit (явное лучше неявного);
- Reliability over velocity (надежность важнее скорости разработки);
- Contracts over assumptions (жесткие контракты важнее предположений);
- Observability first (мониторинг закладывается на этапе проектирования);
- Async by default (асинхронность по умолчанию в тяжелых операциях);
- Governance enforced by CI/CD (все архитектурные правила проверяются машиной);
- Architecture evolves only through ADR (архитектура развивается исключительно документированием в журнале решений).
================================================================================
