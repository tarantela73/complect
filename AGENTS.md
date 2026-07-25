# AGENTS.md

## Обзор

ERP «Комплект» — событийно-ориентированный модульный монолит.

- **Backend**: Django 5.2 + django-ninja (ASGI, Granian), Python 3.12
- **Frontend**: Next.js 15, React 19, TypeScript, Tailwind 4, TanStack Query, Zustand
- **DB**: PostgreSQL 16 + PostGIS, Redis 7 (8 DB: 0–7), Celery (7 очередей)
- **API**: `/api/v1/`, Ninja-generated OpenAPI → автогенерируемый TypeScript SDK
- **Стек**: ASGI-only (Granian), WSGI в production запрещён

Архитектурная спецификация: `docs/Project_architecture.md`
Глоссарий доменов: `CONCEPTS.md`
ADR (23 штуки): `docs/adr/`
Solution docs: `docs/solutions/` (patterns, database-issues, test-failures, dev-experience и др.)
OpenCode plugin: `opencode.json` → [Superpowers](https://github.com/obra/superpowers.git) skills
Память агентов: `docs/development/supermemory.md` — обязательная изоляция проектного и task-контекста через `containerTag`

---

## Команды

Вся разработка через Docker. Команды выполняются из корня `complect/`.

```bash
make up              # Запуск всего стека (--build -d)
make down            # Остановка
make rebuild         # Пересборка (down + up --build)
make destroy         # Остановка + удаление volumes
make logs            # Все логи (--tail=200)
make restart         # Перезапуск контейнеров

make test-all        # Все тесты (pytest -v)
make test            # Только apps/common/tests/test_context_leakage.py
make test-fast       # Быстрые тесты (pytest -x, остановка на первом падении)
make test-failed     # Только упавшие (--lf)
make test-common     # Тесты apps/common/tests/
make test-contracts  # Тесты apps/contracts/tests/
make test-contragents # Тесты apps/contragents/tests/
make test-integration # Тесты apps/integration/tests/
make test-realtime   # Тесты apps/realtime/tests/
make coverage        # Покрытие (pytest --cov)

make lint            # Ruff check
make format          # Ruff format
make types           # Mypy

make migrate         # python manage.py migrate --noinput
make makemigrations  # python manage.py makemigrations

make generate-sdk    # OpenAPI JSON → TypeScript типы
make tsc             # TypeScript typecheck (tsc --noEmit)
make test-e2e        # Playwright e2e тесты

make shell           # Django shell внутри контейнера
make psql            # PostgreSQL консоль
make redis-cli       # Redis консоль
make health          # curl http://localhost:8010/health/
make celery-status   # ping celery worker
```

---

## Codex: агенты, память и MCP

### Project-local агенты

- `complect_explorer` — read-only поиск путей выполнения, ADR и доменных границ.
- `complect_reviewer` — read-only проверка correctness, security, tenant isolation
  и архитектурных инвариантов.
- `complect_verifier` — запуск минимального релевантного набора Docker-проверок
  без изменения исходного кода.
- Делегировать только независимые ограниченные подзадачи. Основной агент
  принимает решения, выполняет изменения и проверяет выводы подагентов.
- Project-local custom roles запускать без полной истории
  (`fork_turns="none"`), передавая самодостаточный brief. Full-history fork
  наследует тип основного агента и несовместим с явным custom agent type.
- Подагенты не читают и не записывают долговременную память самостоятельно.
  Все операции памяти координирует основной агент с заранее выбранным точным
  контейнером.

### Supermemory

- Полный протокол находится в `docs/development/supermemory.md`.
- В начале задачи один раз выбрать `task-key`; не переиспользовать контейнер
  завершённой или несвязанной задачи.
- Проектный и task-контекст читать отдельными операциями с
  `containerTag=complect:project` и
  `containerTag=complect:task:<task-key>`.
- Для каждой операции указывать ровно один `containerTag`. Recall без
  `containerTag` запрещён.
- Не сохранять секреты, токены, PII, полные логи, дампы БД и неподтверждённые
  гипотезы как факты.

### Add-only MCP policy

- Перед изменением проверить глобальный и project config, затем выполнить
  `codex mcp list`.
- По умолчанию разрешено только добавление нового MCP под уникальным ID.
  Удаление, отключение, переименование, замена или переопределение существующего
  MCP и его transport/auth/tool policy требуют явного запроса пользователя с
  точным ID сервера.
- Переиспользуемый MCP добавлять в `~/.codex/config.toml`, нужный только Complect
  — в `.codex/config.toml`; один ID не дублировать в обоих слоях.
- Credentials передавать ссылками на environment variables, не literal
  значениями в TOML. После изменения повторно выполнить `codex mcp list`.

---

## Архитектурные правила

### Изоляция доменов (ADR-0005)

Кросс-импорты моделей/сервисов между доменами **категорически запрещены**.
Общение — через `apps/common/contracts/` (typing.Protocol) или Transactional Outbox.
Линтер: `infra/scripts/ddd_boundary_linter.py`.

```python
# ЗАПРЕЩЕНО
from apps.finance.models.ledger import LedgerTransaction  # внутри apps/contracts/

# РАЗРЕШЕНО
from apps.common.contracts.finance_contracts import FinanceTransactionFinder  # Protocol
```

### Мутации (rule 4.6–4.7)

Мутации **только** через `EnterpriseSreRepository.atomic_cas_update()`. Прямые `.save()` / `.delete()` на моделях запрещены. Soft delete через `is_deleted=True` + `deleted_at=now()`.

### Деньги (rule 4.6)

Суммы хранятся в **копейках** (`BigIntegerField`). Не float, не Decimal в БД.
Конвертация: `int(decimal_rubles * 100)`.

### События (rule 4.5)

```python
@dataclass(slots=True, frozen=True)
class DomainEvent:
    event_id: uuid.UUID
    event_version: int
    occurred_at: datetime
    correlation_id: uuid.UUID
```

Публикация строго: `DB COMMIT → transaction.on_commit() → Redis publish / Celery`.
Публикация до коммита запрещена.

### Пагинация (ADR-0008)

OFFSET-пагинация **запрещена** для: audit logs, finance, notifications, websocket events, chats.
Стандарт: `WHERE id < cursor ORDER BY id DESC`.

### Tenant isolation

Каждый QuerySet **обязан** содержать `tenant_id`. Без фильтра — data leak.
Мутации через `.filter(id=..., tenant_id=...)`, не `.get()` + `.save()`.
Tenant_id извлекается из JWT, не из тела запроса.

### Legal Hold

`is_legal_hold=True` блокирует: soft delete, hard delete, mutation, retention cleanup.
Проверка обязательна перед любой мутацией.

### OpenAPI контракт (ADR-0003)

OpenAPI схема = замороженный контракт для `/api/v1/`.
Breaking changes требуют: ADR + новую major версию `/api/v2/`.
TS SDK: `frontend/src/shared/api/generated/` — ручные правки **запрещены**.
Генерация: `make generate-sdk`.

### Миграции (ADR-0002)

Expand & Contract — 3 релиза:

1. **Release N**: добавить nullable column
2. **Release N+1**: async Celery backfill (батчи 5000, `SKIP LOCKED`)
3. **Release N+2**: `NOT NULL` через `NOT VALID` → `VALIDATE CONSTRAINT`

Запрещено: `AddField(NOT NULL, no default)`, `RemoveField` на активных столбцах, индексы без `CONCURRENTLY`.
Линтеры: `infra/scripts/migration_linter.py`, `infra/scripts/ddd_boundary_linter.py`, `infra/scripts/openapi_contract_linter.py`.

### PII (ADR-0009)

Шифрование: AES-256 через `pgcrypto`. Ключ из Docker Secret / Vault, никогда в коде.
Каждое чтение PII логируется (кто, когда, зачем, request_id, correlation_id).

### RBAC

Три режима: `OFF` (bypass), `LOG_ONLY` (log denials, default), `ENFORCE` (raise PermissionDenied).

**Критичная ловушка**: `is_superuser=True` НЕ даёт permissions фронтенду. Permissions загружаются из `RoleBinding` в БД. Без привязки роли — `permissions: []` → в навигации дашборда видна только «Главная».

Создание RoleBinding (через Django shell):
```python
import uuid
from apps.common.models.rbac import RoleBinding, Role
from apps.users.models import User

u = User.objects.get(username='testadmin')
admin_role = Role.objects.get(name='admin')

# user_id НЕ равен u.pk! Нужен конвертированный UUID:
correct_uuid = uuid.UUID(int=0x00000000000040008000000000000000 | (u.pk & 0xFFFFFFFFFFFF))

RoleBinding.objects.create(user_id=correct_uuid, tenant_id=u.tenant_id, role=admin_role, is_active=True)
```

### Middleware stack (порядок выполнения)

1. `CorsMiddleware` — CORS headers
2. `SecurityMiddleware` — HSTS, XSS filter
3. `ContextIsolationMiddleware` — изоляция контекста запроса
4. `SreGlobalKillSwitchMiddleware` — глобальный kill switch
5. `AsgiTenantEnforcementMiddleware` — trust boundary: Gateway token → tenant/user extraction
6. `IdempotencyEnforcementMiddleware` — идемпотентность (кроме `/auth/`)
7. `ReadYourOwnWritesStickyMiddleware` — sticky routing на primary после записи
8. `StructlogTracingContextMiddleware` — structlog context
9. `PostgresRlsEnforcementMiddleware` — Row-Level Security
10. `SessionMiddleware`, `CommonMiddleware`, `CsrfViewMiddleware`

### API Gateway (Kong)

Все запросы к `/api/v1/` идут через Kong (порт 8000). Три уровня доверия:

- **Public** (`/public/`, `/health/`, `/metrics/`) — без аутентификации
- **Context-Free** (`/api/v1/auth/`, `/docs`, `/openapi.json`) — требуют `X-Gateway-Token`, но без tenant/user
- **Protected** (все остальные) — требуют `X-Gateway-Token` + `X-Tenant-ID` + `X-User-ID`

Gateway token: `$EXPECTED_GATEWAY_TOKEN` (dev).

---

## Структура backend

```
backend/
├── config/
│   ├── asgi.py          # ASGI entry point (REST + WebSocket)
│   ├── api.py           # NinjaAPI instance
│   ├── urls.py          # API routing (/api/v1/...)
│   ├── celery.py
│   ├── health_urls.py
│   └── settings/
│       ├── base.py      # Общие настройки
│       ├── local.py     # Dev (DJANGO_SETTINGS_MODULE=config.settings.local)
│       └── prod.py
└── apps/
    ├── common/          # Инфраструктура: outbox, tenancy, events, security, RBAC
    │   ├── contracts/   # Internal SDK (typing.Protocol)
    │   ├── events/      # @dataclass(slots=True, frozen=True)
    │   ├── repository/  # EnterpriseSreRepository
    │   ├── middleware/   # Context isolation, kill switch, idempotency, RLS
    │   ├── tenancy/     # Tenant enforcement middleware
    │   └── models/      # BaseEnterpriseModel, OutboxRecord, DLQ, RBAC
    ├── users/           # Auth, RBAC, MFA, JWT, sessions
    ├── contracts/       # Договоры, ДС, Акты
    ├── contragents/     # Контрагенты, валидаторы ИНН/ОГРН
    ├── finance/         # Транзакции, платежи, баланс
    ├── objects/         # GIS: PostGIS, spatial queries
    ├── files/           # File pipeline: ClamAV, S3
    ├── realtime/        # WebSocket, Presence, LiveKit
    ├── integration/     # 1С ACL, Inbox pattern, webhooks
    ├── notifications/   # Email, Telegram, In-App
    ├── payments/        # Bank statement import, reconciliation
    ├── reports/         # Отчётность
    ├── requests/        # Заявки
    ├── site/            # CMS, публичный сайт
    ├── map/             # GIS-карты
    └── tasks/           # Задачи и комментарии (CQRS, event-driven)
```

Каждое app содержит: `models/`, `services/`, `queries/`, `schemas/`, `api.py`, `tests/`.

### Celery очереди (rule 4.15)

| Очередь | Назначение | Priority |
|---|---|---|
| `default` | Общие задачи | normal |
| `outbox` | Transactional Outbox dispatcher | high |
| `notifications` | Email, Telegram, In-App | normal |
| `finance` | Финансовые пересчёты, выписки | high |
| `files` | ClamAV pipeline, S3 upload | low |
| `retention` | Очистка по retention policy | low |
| `integration` | Webhook fanout | normal |

`PREFETCH_MULTIPLIER=1`, `acks_late=True`, `max_retries=5`.

### Redis DB (rule 4.15)

| DB | Назначение |
|---|---|
| 0 | Default |
| 1 | Cache |
| 2 | Celery broker |
| 3 | Streams |
| 4 | Idempotency |
| 5 | Channel layer (WebSocket) |
| 6 | Celery result backend |
| 7 | Audit |

---

## Тестирование

- **pytest внутри Docker контейнера** (не локально!)
- **SQLite в тестах категорически запрещён** — только PostgreSQL + PostGIS
- `asyncio_mode = auto` — async-тесты не требуют декоратора
- `conftest.py` автоматически: (1) устанавливает `pgcrypto`, (2) добавляет `replica` DB ко всем django_db тестам, (3) блокирует `apply_async` на outbox dispatcher чтобы live celery не крал задачи из тестов
- E2E: `cd frontend/e2e && npx playwright test` (Playwright, Chromium, headless, `ru-RU`, `Europe/Moscow`)
- Playwright `baseURL` = `http://localhost:3000` (container port, не host-mapped 8080)
- Frontend unit: `vitest run`

---

## Сервисы и порты

| Сервис | Порт | Описание |
|---|---|---|
| backend | 8010 | Granian ASGI (Django) |
| frontend | 8080 (host) → 3000 (container) | Next.js dev server |
| api_gateway (Kong) | 8000 | API Gateway (JWT, rate limiting, CORS) |
| postgres_primary | 5432 | PostgreSQL 16 + PostGIS |
| postgres_replica | 5433 | Read replica |
| redis | 6379 | Redis 7.2 (DB 0–7) |
| minio | 9000/9001 | S3-compatible storage |
| clamav | 3310 | Antivirus daemon |
| jaeger | 16686 | Distributed tracing UI |
| prometheus | 9090 | Metrics |
| grafana | 3001 | Dashboards |
| otel_collector | 4317 | OpenTelemetry collector |

Health check: `curl http://localhost:8010/health/`

---

## Кодстайл

### Python (ruff)

- `line-length = 120`
- `target-version = "py312"`
- Миграции исключены из линтинга
- Rules: `E, F, B, I, N` (D игнорируется)
- Ninja sentinel markers в `extend-immutable-calls`: `ninja.Query`, `ninja.Body`, `ninja.Form`, `ninja.Header`, `ninja.Cookie`, `ninja.Path`

### TypeScript

- Strict mode
- Path alias: `@/*` → `./src/*`
- Dev server: `next dev --turbopack`

---

## Каузальная модель событий (ADR-0015–0019)

Система отслеживает причинно-следственные цепочки событий через `CausalUnit`:

- `outbox_record_id` — identity конкретного dispatch
- `causal_root_id` — root всей причинной цепи (неизменен при repair)
- `correlation_id` — cross-request trace

Три каузальных инварианта: Sequence Anchoring, DLQ Causal Preservation, Repair Root Immutability.
Chain Completeness SLO: 99.9% за 5 минут.

---

## Важные паттерны (docs/solutions/)

- **Transactional Outbox**: `push_to_outbox()` строго внутри `transaction.atomic()`, после мутации
- **Tenant isolation**: 5 правил — все QuerySet с `tenant_id`, мутации через `.filter()`, не `.save()`
- **Cursor pagination**: `WHERE id < cursor ORDER BY id DESC` для критических коллекций
- **PATCH UNSET sentinel**: PATCH с `null` не应当 clear поле; используйте sentinel value
- **Cross-domain aggregation**: делегируйте агрегацию через Protocol, не прямые JOIN
- **Kong CORS**: CORS настраивается на уровне Kong, а не только в Django. Проверять через порт 8000.

---

## Dev-окружение

### Быстрый старт

```bash
make up                    # Запустить стек (backend автоматически делает migrate)
make createsuperuser       # Создать админа (если нет)
```

Backend контейнер автоматически выполняет `migrate --noinput` при старте (перед запуском Granian).

### Логин через curl (для проверки API)

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -H "X-Gateway-Token: $EXPECTED_GATEWAY_TOKEN" \
  -d "{\"username\":\"testadmin\",\"password\":\"${TESTADMIN_PASSWORD}\",\"device_fingerprint\":\"cli-agent-001\"}"
```

### Тестовый пользователь

- Username: `testadmin`, Password: `$TESTADMIN_PASSWORD` (создан через `set_password`)
- Role: `admin` (30 permissions), tenant: `a1b2c3d4-e5f6-4a7b-8c9d-e0f1a2b3c4d5`

### Frontend auth flow

- Zustand store, хранится в памяти (не localStorage)
- JWT payload: `user_id`, `tenant_id`, `username`, `exp`, `type`
- Permissions приходят в ответе `/auth/login` → сохраняются в Zustand
- Навигация дашборда фильтруется через `useCan(permission)` — без permissions видна только «Главная»

### Известные проблемы (docs/solutions/)

- **Docker Desktop WSL2 ghost port**: порт 5432 может заниматься мёртвым контейнером → `docker compose down` + ручная очистка
- **Outbox lock contention**: тесты с `transaction=True` могут пропускать dispatched tasks из-за заблокированных записей
- **Streaming replica stale reads**: в тестах с `transaction=True` replica возвращает устаревшие данные → используйте `transaction=False` или читайте из primary

---

## Чего НЕ делать

- Не импортировать модели/сервисы из чужого домена напрямую
- Не вызывать `.save()` / `.delete()` на моделях — только через репозиторий
- Не использовать OFFSET для audit/finance/notifications/chat
- Не редактировать TS SDK вручную
- Не публиковать события до `transaction.on_commit()`
- Не принимать `tenant_id` из тела запроса — только из JWT
- Не использовать SQLite в тестах
- Не добавлять `NOT NULL` поле без default в миграции
- Не создавать RoleBinding с `user_id=u.pk` — нужен `uuid.UUID(int=0x00000000000040008000000000000000 | (u.pk & 0xFFFFFFFFFFFF))`
