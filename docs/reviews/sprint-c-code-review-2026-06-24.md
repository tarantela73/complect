# Code Review — Sprint C: Operational Maturity

**Branch:** `feat/sprint-c-operational-maturity` → `main`  
**Date:** 2026-06-24  
**Reviewers:** 13 parallel subagents (correctness, testing, maintainability, project-standards, security, performance, api-contract, data-migration, reliability, adversarial, agent-native, learnings, deployment-verification)  
**Scope:** Tasks domain, Director Dashboard, Cursor Pagination fix, RBAC seeds, Notification events, Infra patch

---

## Verdict: CONDITIONAL_GO

**Блокеры перед мержем: 2 P0, 14 P1**

---

## P0 — Критические (блокируют merge)

| ID | Файл | Описание |
|----|------|----------|
| **SEC-1** | `backend/config/settings/base.py:335` | **RBAC_ENFORCEMENT_MODE='LOG_ONLY'** — все @require_permission декораторы только логируют, не блокируют. Любой аутентифицированный пользователь может вызвать `/dashboard/director/` и любой mutating tasks endpoint. |
| **STD-1** | `backend/apps/common/queries/director_queries.py:14` | **ADR-0005 нарушение** — `director_queries.py` в `apps/common/` импортирует ORM-модели из 4 доменов: `Contract` (apps.contracts), `SupplyObject` (apps.objects), `BankStatement`, `BankStatementLine` (apps.payments). CI должен блокировать. |

**Fix SEC-1:** Установить `RBAC_ENFORCEMENT_MODE = 'ENFORCE'` в `config/settings/prod.py`.  
**Fix STD-1:** Вынести функции агрегации в соответствующие domain `queries/` (по аналогии с уже существующим `get_contract_counts()` в `contracts/queries/`).

---

## P1 — Высокий приоритет (должны быть исправлены)

### Безопасность

| ID | Файл:строка | Описание | Fix |
|----|------------|----------|-----|
| **SEC-3** | `settings/local.py:108` | `EXPECTED_GATEWAY_TOKEN` захардкожен в sources (`'complect_internal_secure_mesh_token_2026'`). Любой с доступом к репо может обойти gateway trust boundary. | `os.environ['EXPECTED_GATEWAY_TOKEN']` без default. Ротировать токен. |
| **SEC-4** | `create_test_users.py:121` | `getenv('ENVIRONMENT', 'local')` — если в prod не задан ENVIRONMENT, команда создаёт директора с паролем `'Test123!'`. | Использовать `settings.DEBUG` как gate. |
| **SEC-2** | `apps/tasks/api.py:166` | `detach_file` не проверяет ownership — любой с `delete:tasks` может удалить чужое вложение. | Передать `actor_id` в `detach_file()`, проверить `attachment.attached_by_id == actor_id`. |
| **ADV-3/SEC-6** | `tasks/services/task_service.py:36` | `assigned_to_id` принимается без проверки принадлежности к тенанту — IDOR, уведомления уходят в чужой namespace. | `User.objects.filter(id=assigned_to_id, tenant_id=tenant_id).exists()` в create/update. |

### Корректность

| ID | Файл:строка | Описание | Fix |
|----|------------|----------|-----|
| **COR-1** | `contract_queries.py:59` | **FTS + cursor несовместимы**: `ORDER BY SearchRank DESC, created_at, id` но cursor фильтрует только по `(created_at, id)` → страницы перекрываются/пропускаются. | При `q + cursor`: вернуть первую страницу или HTTP 400 `INVALID_CURSOR_WITH_SEARCH`. |
| **COR-2** | `apps/tasks/api.py:71` | **Post-write reads на replica** нарушает rule 4.13: `get_task_detail()` после create/update идёт на replica, которая может не получить запись. | `set_force_primary_db(True)` перед read после мутации; или вернуть in-memory объект. |
| **COR-3/REL-3/API-2** | `contract_queries.py:72` | **Cursor anchor=None → page1 возвращается молча** (deleted record, wrong tenant). Клиент получает дубли. То же в `task_queries.py`. | `if cursor and anchor is None: return {'results': [], 'next_cursor': None, 'count': 0}` |

### Reliability

| ID | Файл:строка | Описание | Fix |
|----|------------|----------|-----|
| **REL-1** | `api/dashboard.py:103` | Redis недоступен → 500 на `/dashboard/director/`. `RedisCache` не подавляет `ConnectionError`. | `try/except redis.exceptions.RedisError` вокруг cache.get/set; или `django-redis IGNORE_EXCEPTIONS=true`. |
| **REL-2** | `contract_queries.py:67` | Cursor anchor lookup идёт в **primary** (обходит `ContractRepository.get_queryset()` → replica routing не применяется). При replica lag > 0: anchor на primary есть, main qs на replica ещё нет. | Заменить `Contract.objects.filter(...)` на `ContractRepository.get_queryset().filter(id=cursor)...`. |
| **REL-4** | `common/events/tasks.py:7` | `TaskCompletedEvent` / `TaskCancelledEvent` не имеют `tenant_id` поля. При вызове из Celery/management command без context — stream роутится в `'erp:streams:tenant:global'`, уведомления теряются. | Добавить `tenant_id: uuid.UUID` в оба dataclass; заполнять из `task.tenant_id`. |

### Стандарты проекта

| ID | Файл:строка | Описание | Fix |
|----|------------|----------|-----|
| **STD-2** | `task_service.py:191` | Raw `attachment.save()` в `detach_file()` без CAS — concurrent detach не обнаружит конфликт. | `TaskAttachmentRepository.soft_delete()` с `filter(version=N).update(...)`. |
| **LRN-2** | `migrations/0002_taskattachment_partial_unique.py:18` | Partial unique index `(task_id, file_id) WHERE is_deleted=FALSE` **без `tenant_id`** — cross-tenant коллизия возможна если file_id UUID глобально уникальны. | `CREATE UNIQUE INDEX ... ON tasks_attachments (tenant_id, task_id, file_id) WHERE is_deleted = FALSE` |

### Тестирование

| ID | Файл:строка | Описание | Fix |
|----|------------|----------|-----|
| **TST-1** | `test_tasks_api.py:182` | Нет теста `IN_PROGRESS → DONE` — ветка `TaskCompletedEvent` через Outbox не покрыта. | Добавить тест: create → in_progress → done; assert OutboxRecord с event_type='tasks.TaskCompleted'. |
| **TST-2** | `test_notifications.py:50` | Нет тестов `TaskCompletedHandler` / `TaskCancelledHandler` — registration, recipient extraction, deduplication. | 5 unit-тестов handler'ов. |
| **TST-3** | `test_director_dashboard.py:315` | Objects KPI тестируются только на empty state — баг "всегда пусто" прошёл бы. | Создать `SupplyObject` с `status=IN_PROGRESS`, проверить count/top5/overdue. |

### API / SDK

| ID | Файл:строка | Описание | Fix |
|----|------------|----------|-----|
| **API-1** | `schema.d.ts:3236` | **TS SDK stale**: `tenant_id` есть в сгенерированной схеме как `required`, но Python `TaskOut` его не объявляет. TS код получит `undefined` вместо `string`. | `make generate-sdk` после финального `task_dto.py`. |

---

## P2 — Средний приоритет

### Архитектура/стандарты

| ID | Описание |
|----|----------|
| **SEC-5** | Бухгалтер получает `write:tasks` — финансовая роль без оперативного мандата. Снизить до `read:tasks`. |
| **STD-4** | Пароли `'Test123!'` в create_test_users — 8 символов, минимум 12 (rule 5.1). |
| **MNT-4** | `get_recipient_ids()` дублирован byte-for-byte в `TaskCompletedHandler` и `TaskCancelledHandler`. Вынести в `TaskBaseHandler`. |
| **MNT-3** | `TaskAttachment.Meta` пустой — partial unique index невидим для ORM (`makemigrations` слеп). Добавить `UniqueConstraint(condition=Q(is_deleted=False), ...)`. |
| **MNT-7** | `create_test_users` хардкодит `PUBLIC_SITE_TENANT_ID` UUID вместо `settings.PUBLIC_SITE_TENANT_ID`. |
| **REL-5** | `TaskService.create()` не выпускает `TaskCreatedEvent` через Outbox — новая задача не уведомляет получателей. |
| **REL-6** | `run_notifications_consumer_task` без `autoretry_for` / backoff — DB error → немедленный retry storm. |

### Производительность

| ID | Описание |
|----|----------|
| **PERF-1** | Cursor anchor lookup — дополнительный SELECT на каждую страницу (2 запроса вместо 1). Решение: кодировать `(created_at, id)` в cursor value. |
| **PERF-2** | `get_objects_summary()` — 3 отдельных DB запроса вместо 1-2. |
| **PERF-4** | Anchor lookup фильтрует `is_deleted=False` — cursor на soft-deleted record сбрасывает пагинацию на page1. Убрать `is_deleted=False` из anchor lookup. |
| **PERF-6** | `invalidate_director_dashboard()` определён, но нигде не вызывается — KPI кэш не инвалидируется при мутациях. |

### Тестирование

| ID | Описание |
|----|----------|
| **TST-4** | Нет теста cursor + status filter в комбинации. |
| **TST-6** | Нет теста редактирования задачи в terminal state (DONE/CANCELLED). |
| **TST-7** | Нет теста что assignee (не creator) может обновить задачу. |

### Миграции

| ID | Описание |
|----|----------|
| **MIG-1** | `Task.Meta.indexes` без `name=` → spurious migration на каждый `makemigrations`. Добавить `name='tasks_tasks_tenant_status_idx'` etc. |
| **MIG-2** | `0002`: `AlterUniqueTogether` + `CONCURRENTLY` в одном `atomic=False` — окно без uniqueness guarantee при сбое index build. Разбить на 2 миграции. |
| **MIG-4** | `0026_seed_director_dashboard_permission` — untracked, нет формального review. Добавить в git. |

### API

| ID | Описание |
|----|----------|
| **API-4** | `TaskListOut.items` — все остальные list endpoints используют `results`. Переименовать. |
| **API-5** | `POST /{task_id}/files/` возвращает untyped `dict` — attachment `id` нужен для DELETE но не типизирован. |
| **AGT-3** | Director dashboard не содержит tasks KPI (`open_tasks_count`, `overdue_tasks_count`). |
| **AGT-4** | Date range захардкожен на текущий месяц — нельзя запросить исторический период. |

---

## P3 — Низкий приоритет (на усмотрение)

| ID | Описание |
|----|----------|
| **PERF-5** | `0007`: `is_deleted` избыточен внутри partial index `WHERE is_deleted=FALSE`. |
| **MIG-3** | То же: `is_deleted` в `0007` — 4 лишних байта. |
| **PERF-7** | FTS path не использует cursor index — документировано как known limitation. |
| **SEC-7** | `director_queries` вызывает `get_tenant_id()` повторно внутри функций — structural risk при context leak. Передавать `tenant_id` параметром. |
| **SEC-8** | `TaskOut` возвращает `tenant_id` клиенту — unnecessary disclosure. |
| **AGT-5** | `TaskListOut.next_cursor: Optional[uuid.UUID]` вместо `Optional[str]` — несоответствие с `ContractListOut`. |
| **COR-7/MIG-1** | `Task.Meta.indexes` без `name=` (уже в P2). |
| **ADV-6** | `0025 unseed` удаляет ВСЕ RolePermissions для tasks, включая вручную добавленные после seed. |
| **LRN-5** | Новые Celery handlers не проверены на наличие в `_block_outbox_live_dispatch` fixture. |
| **AGT-1** | `TaskComment` — упомянут в scope Sprint C, не реализован. |

---

## Coverage

### Tracked в diff (reviewed)
- `backend/apps/contracts/queries/contract_queries.py` — cursor pagination fix ✓
- `backend/apps/common/api/dashboard.py` — director dashboard endpoint ✓
- `backend/apps/common/queries/director_queries.py` — KPI aggregation ✓
- `backend/apps/common/security/rbac_permissions.py` ✓
- `backend/apps/common/migrations/0025_seed_tasks_permissions.py` ✓
- `backend/apps/tasks/models/task_attachment.py` ✓
- `backend/apps/users/management/commands/create_test_users.py` ✓
- `backend/config/settings/local.py` ✓
- `docker-compose.dev.yml` ✓

### Untracked (не в diff, reviewed с диска)
- `backend/apps/tasks/` (весь домен) — reviewed fully
- `backend/apps/common/events/tasks.py` — reviewed
- `backend/apps/notifications/handlers/task_handlers.py` — reviewed
- `backend/apps/common/migrations/0026_seed_director_dashboard_permission.py` — **не staging'd, review неполный**
- `backend/apps/contracts/migrations/0009_contracts_cursor_index.py` — reviewed
- `backend/apps/objects/migrations/0007_supply_object_overdue_index.py` — reviewed
- `frontend/src/app/dashboard/director/page.tsx` — **не reviewed** (frontend)

---

## Pre-Deploy Checklist (CONDITIONAL_GO)

1. Установить `RBAC_ENFORCEMENT_MODE = 'ENFORCE'` в prod settings
2. `git add` все untracked files (включая 0026, task domain files)
3. Исправить `STD-1`: вынести `director_queries.py` агрегации по доменам
4. Исправить `COR-3`: cursor anchor=None → пустой ответ, не page1
5. Исправить `REL-4`: добавить `tenant_id` в `TaskCompletedEvent` / `TaskCancelledEvent`
6. Исправить `API-1`: регенерировать TS SDK (`make generate-sdk`)
7. Добавить `LRN-2`: `tenant_id` в partial unique index `tasks_attachments`
8. `make test-all` → 0 failures
9. Подтвердить что `tasks_tasks` и `tasks_attachments` не существуют в prod DB
10. Проверить роли в `common_roles`: director, manager, accountant, foreman, admin
11. Проверить отсутствие долгих транзакций перед CONCURRENTLY index migrations

---

## Deployment Checklist (after approve)

```sql
-- Verify cursor index created
SELECT indexname, indexdef FROM pg_indexes WHERE indexname = 'contracts_contracts_cursor_idx';

-- Verify task tables exist  
SELECT tablename FROM pg_tables WHERE tablename IN ('tasks_tasks', 'tasks_attachments');

-- Verify partial unique index
SELECT indexname, indexdef FROM pg_indexes WHERE indexname = 'tasks_attachments_active_unique';

-- Verify RBAC seeds
SELECT p.action, p.resource, r.name FROM common_permissions p 
JOIN common_rolepermissions rp ON rp.permission_id = p.id 
JOIN common_roles r ON r.id = rp.role_id 
WHERE p.resource IN ('tasks', 'director_dashboard') ORDER BY p.resource, p.action, r.name;
```
