---
title: "Sprint A — Исправление блокеров ERP: PATCH мутации, contract_date, удаление файлов, Excel экспорт"
date: "2026-06-20"
status: completed
sprint: A
priority: P0
origin: docs/brainstorms/2026-06-20-sprint-a-erp-blockers-requirements.md
domains:
  - contragents
  - contracts
  - files
  - reports
---

# Sprint A — Исправление блокеров ERP

## Problem Frame

Шесть P0-дефектов блокируют мутирующие операции в ERP. Без них система фактически read-only.

Подтверждённые root causes:
- **P0.1, P0.2**: `select_for_update()` вызывается вне `transaction.atomic()` → Django 5.2 поднимает `TransactionManagementError` в autocommit-режиме.
- **P0.3**: `ContractActivateService` корректен (select_for_update внутри atomic). Вероятная причина — RBAC или UI условие `canWrite && isMutable && isDraft`. Диагностика — часть U2.
- **P0.4**: Поле `contract_date` полностью отсутствует в модели, DTO и UI.
- **P0.5**: Manager-роль не имеет `files.delete` → кнопка не отображается (`useCan('files.delete') = false`). Admin роль имеет разрешение.
- **P0.6**: `downloadReport()` открывает `<a href>` без Authorization header → JWT middleware не устанавливает `jwt_tenant_id` → `require_permission` → 403.

## Scope Boundaries

**In scope**: исправление P0.1–P0.6 из Sprint A Requirements.

**Out of scope**: удаление контрагентов, 360° связи контрагент↔договоры/объекты, именованные типы документов для закрытия, async select при привязке объекта, уведомления — всё это Sprint B/C.

## Requirements Trace

| Unit | P0 | Затронутый домен |
|------|----|-----------------|
| U1 | P0.1 — Обновление контрагента | `apps/contragents` |
| U2 | P0.2 + P0.3 — Обновление + активация договора | `apps/contracts` |
| U3 | P0.4 — Дата договора конец-в-конец | `apps/contracts`, `frontend` |
| U4 | P0.5 — Удаление файла: RBAC | `apps/common`, `apps/files` |
| U5 | P0.6 — Excel экспорт: авторизованный fetch | `frontend` |

---

## U1 — Fix update_contragent: select_for_update inside atomic

**Goal**: Починить `PATCH /api/v1/contragents/{id}/` — 500/TransactionManagementError при сохранении контрагента.

**Decision**: Обернуть весь блок `select_for_update().get()` + мутация в единый `with transaction.atomic():`. `select_for_update()` требует активной транзакции; Django 5.2 поднимает исключение в autocommit-режиме.

**Files**:
- Modify: `backend/apps/contragents/api.py` — `update_contragent` endpoint
- Create: `backend/apps/contragents/tests/test_contragent_api.py`

**Approach**:

```python
# Текущий (сломанный) паттерн в update_contragent:
contragent = Contragent.objects.select_for_update().get(...)  # вне atomic — BUG
...
with transaction.atomic():  # слишком поздно
    contragent.save(...)

# Правильный паттерн (как в ContractActivateService):
with transaction.atomic():
    contragent = Contragent.objects.select_for_update().get(
        id=contragent_id, tenant_id=tenant_id, is_deleted=False
    )
    if contragent.is_legal_hold:
        return 422, ...
    contragent.name = body.name
    contragent.version += 1
    contragent.save(update_fields=["name", "version", "updated_at"])
```

**Patterns to follow**: `backend/apps/contracts/services/contract_activate_service.py` (select_for_update внутри atomic), `backend/apps/map/tests/test_search_api.py` (тест-паттерн с `HTTP_X_GATEWAY_TOKEN`)

**Test scenarios**:
- `test_update_contragent_success`: PATCH с `{ name: "New Name" }` → 200, обновлённое name в ответе
- `test_update_contragent_not_found`: PATCH несуществующего ID → 404
- `test_update_contragent_legal_hold`: PATCH на `is_legal_hold=True` → 422
- `test_update_contragent_wrong_tenant`: PATCH ID другого тенанта → 404 (tenant isolation)

**Verification**: `pytest apps/contragents/tests/ -v` — все passing; PATCH 200 в браузере на `/dashboard/contragents/{id}/edit`

---

## U2 — Fix update_contract + verify activation

**Goal**: Починить `PATCH /api/v1/contracts/{id}/` и устранить блокер активации DRAFT-договора.

**Decision (P0.2)**: `ContractRepository.get_by_id_for_update(contract_id)` в `apps/contracts/api.py:update_contract` (line 89) вызывается вне `transaction.atomic()`. Исправление: обернуть `get_by_id_for_update()` + бизнес-проверки + `atomic_compare_and_swap()` в единый `with transaction.atomic():`.

**Decision (P0.3)**: `ContractActivateService.execute()` корректен. Первый шаг — ручная проверка `POST /api/v1/contracts/{id}/activate/` через API-клиент. Если 200 — проблема в UI (условие `canWrite && isMutable && isDraft` на frontend). Если 403/422/500 — диагностировать по `error.code` и исправить.

**Files**:
- Modify: `backend/apps/contracts/api.py` — `update_contract` endpoint (строки 82-113)
- Create: `backend/apps/contracts/tests/test_contracts_api.py`
- No change to: `backend/apps/contracts/services/contract_activate_service.py` (корректен)

**Approach**:

```python
# update_contract: обернуть в transaction.atomic()
with transaction.atomic():
    try:
        contract = ContractRepository.get_by_id_for_update(contract_id)
    except PermissionDenied:
        return 404, ...
    if contract.is_legal_hold:
        return 403, ...
    if contract.status != ContractStatus.DRAFT:
        return 422, ...
    updates = {}
    if body.title is not None:
        updates["title"] = body.title
    if body.total_amount is not None:
        updates["total_amount"] = body.total_amount
    if updates:
        success = ContractRepository.atomic_compare_and_swap(contract, **updates)
        if not success:
            return 409, ...
return 200, get_contract_detail(contract_id)
```

Для P0.3: если ручная проверка activate показывает 403 → проверить RBAC seed данные для тестового пользователя. Если 422 → логика `ContractActivateService` поднимает `ValidationError` (договор уже не в DRAFT). Если UI-проблема → проверить `isMutable` вычисление на frontend.

**Patterns to follow**: `backend/apps/contracts/services/contract_activate_service.py`, `backend/apps/contracts/tests/test_closure_service.py` (фикстуры и структура тестов)

**Test scenarios**:
- `test_update_contract_draft_success`: PATCH DRAFT с `{ title: "New" }` → 200, поле обновлено
- `test_update_contract_non_draft_blocked`: PATCH ACTIVE-договора → 422
- `test_update_contract_occ_conflict`: concurrent PATCH (старая version) → 409
- `test_activate_contract_success`: POST activate DRAFT → 200, status=ACTIVE, Outbox запись создана
- `test_activate_contract_idempotency`: повторный activate ACTIVE → 422

**Verification**: `pytest apps/contracts/tests/ -v` — все passing; PATCH 200 и Activate → ACTIVE в браузере

---

## U3 — Add contract_date end-to-end

**Goal**: Добавить поле `contract_date` (дата подписания) в модель, DTO, API и UI — create/edit/detail.

**Decision**: Release N (Sprint A) — только nullable `DateField`. NOT NULL, DEFAULT и индекс запрещены на этом этапе (ADR-0002 Expand & Contract). Release N+1 — при необходимости backfill и NOT NULL VALID.

**Files**:
- Modify: `backend/apps/contracts/models/contract.py` — добавить `contract_date = models.DateField(null=True, blank=True)`
- Create: `backend/apps/contracts/migrations/0007_add_contract_date.py`
- Modify: `backend/apps/contracts/schemas/contract_dto.py` — добавить в Create/Update/Response/Detail schemas
- Modify: `backend/apps/contracts/services/contract_create_service.py` (или где создаётся Contract) — передать `contract_date`
- Modify: `backend/apps/contracts/queries/contract_queries.py` — включить `contract_date` в SELECT/аннотацию
- Modify: `frontend/src/app/dashboard/contracts/create/page.tsx` — date input
- Modify: `frontend/src/app/dashboard/contracts/[id]/edit/page.tsx` — date input
- Modify: `frontend/src/app/dashboard/contracts/[id]/page.tsx` — строка "Дата договора"
- Regenerate: `frontend/src/shared/api/generated/` — после `make generate-sdk`

**Approach**:

1. **Модель** — добавить поле в `Contract`:
   ```python
   contract_date = models.DateField(null=True, blank=True)
   ```
2. **Миграция** — создать `0007_add_contract_date.py`. Проверить: только `AddField(null=True)`, без `NOT NULL`, без индекса, с `reverse_sql = ""` или `backwards()`.
3. **Схемы** (`contract_dto.py`):
   ```python
   # Create/Update
   contract_date: Optional[date] = None
   # Response/Detail
   contract_date: Optional[date]
   ```
4. **Сервис создания** — передать `contract_date=body.contract_date` (или `None`) при `Contract.objects.create(...)`.
5. **Queries** — убедиться что `contract_date` включён в SELECT (если используется `.values()` или явные поля).
6. **Frontend**:
   - Create/Edit: нативный `<input type="date">` (Sprint A достаточно; shadcn DatePicker — Sprint B)
   - Detail: `<Row label="Дата договора" value={data.contract_date ? new Intl.DateTimeFormat('ru-RU').format(new Date(data.contract_date)) : '—'} />`
7. **SDK regen**: запустить `make generate-sdk` ПОСЛЕ всех изменений backend DTO, не раньше.

**Patterns to follow**: другие `Optional[date]` поля в `apps/contracts/schemas/`; `Row` компонент в `frontend/src/app/dashboard/contracts/[id]/page.tsx`; `<input type="date">` в существующих frontend формах

**Deferred to implementation**:
- Если `ContractCreateService` создаёт через репозиторий а не прямой ORM — адаптировать вызов под существующий паттерн
- Если поле нужно во `ContractUpdateSchema` — проверить нужна ли отдельная `atomic_compare_and_swap` для `contract_date` (скорее да, добавить в allowed `updates` в U2)

**Test scenarios**:
- `test_create_contract_with_date`: POST с `contract_date: "2026-01-15"` → 201, поле в ответе
- `test_create_contract_without_date`: POST без `contract_date` → 201, `contract_date: null`
- `test_update_contract_date`: PATCH DRAFT с `{ contract_date: "2026-06-01" }` → 200, поле обновлено
- `test_contract_response_includes_date_field`: GET → `contract_date` присутствует в структуре ответа

**Verification**: `make makemigrations && make migrate`; `pytest apps/contracts/tests/ -v`; `make generate-sdk` без ошибок; поле отображается в create/edit/detail в браузере

---

## U4 — Fix file delete: RBAC for Manager role

**Goal**: Дать Manager-роли право `files.delete` через новую seed-миграцию.

**Decision**: Из `0016_seed_rbac_data.py` — Manager роль не имеет `("delete", "files")`. Admin имеет. Поскольку менеджеры должны управлять файлами (P0 из requirements), добавить разрешение seed-миграцией. UI (`useCan('files.delete')` и DELETE-endpoint) уже корректно реализованы.

**Files**:
- Create: `backend/apps/common/migrations/0024_seed_files_delete_manager.py`
- Test: extend `backend/apps/files/tests/test_file_list_and_delete.py` или создать `backend/apps/files/tests/test_file_delete_rbac.py`

**Approach**:

Паттерн по `0019_seed_ops_permission.py`:
```python
from django.db import migrations

def seed_files_delete_manager(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Role = apps.get_model("common", "Role")
    RolePermission = apps.get_model("common", "RolePermission")

    perm, _ = Permission.objects.get_or_create(action="delete", resource="files")
    manager_role = Role.objects.get(name="manager")
    RolePermission.objects.get_or_create(role=manager_role, permission=perm)

def reverse_seed(apps, schema_editor):
    RolePermission = apps.get_model("common", "RolePermission")
    Permission = apps.get_model("common", "Permission")
    Role = apps.get_model("common", "Role")
    try:
        perm = Permission.objects.get(action="delete", resource="files")
        manager_role = Role.objects.get(name="manager")
        RolePermission.objects.filter(role=manager_role, permission=perm).delete()
    except (Permission.DoesNotExist, Role.DoesNotExist):
        pass

class Migration(migrations.Migration):
    dependencies = [("common", "0023_seed_cms_publish_permission")]
    operations = [migrations.RunPython(seed_files_delete_manager, reverse_seed)]
```

**Patterns to follow**: `backend/apps/common/migrations/0019_seed_ops_permission.py`

**Test scenarios**:
- `test_manager_can_soft_delete_file`: пользователь с manager-ролью вызывает `FileRepository.soft_delete(file_id)` → `True`; файл помечен `is_deleted=True`
- `test_manager_soft_delete_legal_hold`: файл с `is_legal_hold=True` → `False`, файл не удалён
- Опционально: API-тест с реальным RBAC (через HTTP_X_GATEWAY_TOKEN без RBAC_ENFORCEMENT_MODE="OFF")

**Verification**: `make migrate`; `pytest apps/files/tests/ -v`; в браузере (manager-пользователь) кнопка "Удалить" видна в `/dashboard/files`

---

## U5 — Fix Excel export: authenticated fetch

**Goal**: Починить кнопки "Экспорт в Excel" в reports — скачивать файл с Authorization header.

**Decision**: Заменить `downloadReport(url, filename)` (использует `<a href>` без токена) на `authenticatedDownload(url, filename)` по паттерну `downloadPdf` — `fetch` + blob + ObjectURL. Backend изменений не требуется.

**Files**:
- Modify: `frontend/src/shared/api/reports.ts` — заменить реализацию `downloadReport` или добавить `authenticatedDownload` и обновить все вызовы

**Approach**:

```typescript
// Новая функция (паттерн из downloadPdf):
async function authenticatedDownload(url: string, filename: string): Promise<void> {
  const token = getAccessToken()  // из frontend/src/shared/api/client.ts
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!response.ok) {
    throw new Error(`Export failed: ${response.status}`)
  }
  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(objectUrl)
}
```

Найти все call-site `downloadReport(...)` в `frontend/src/` и заменить на `authenticatedDownload(...)`.

**Deferred to implementation**:
- Проверить точную сигнатуру `getAccessToken()` в `frontend/src/shared/api/client.ts` перед использованием (может быть `getAccessToken(): string | null` — нужна null-guard)
- Если `downloadReport` используется в нескольких файлах — обновить все вызовы или сделать обратно совместимую обёртку

**Patterns to follow**: `downloadPdf` функция в `frontend/src/shared/api/reports.ts`

**Test scenarios** (manual + browser-qa):
- Залогиненный admin: кнопка "Экспорт в Excel" в `/dashboard/reports/contracts` → скачивается `.xlsx`
- Аналогично для `/dashboard/reports/finance` и `/dashboard/reports/payments`
- Незалогиненный: кнопка выводит ошибку, файл не скачивается

**Verification**: ручное тестирование через `browser-qa`; проверить Network tab → fetch запрос с `Authorization: Bearer ...` header → ответ 200 с `Content-Type: application/vnd.openxmlformats...`

---

## Dependencies & Sequencing

```
U1 (contragents PATCH)  ─── independent
U2 (contracts PATCH + activation)  ─── independent
U3 (contract_date)  ─── depends on U2 finishing first (U2 может добавить contract_date в updates map)
                        SDK regen — только после всех backend DTO изменений
U4 (file delete RBAC)  ─── independent
U5 (Excel export)  ─── independent
```

U1, U2, U4, U5 можно выполнять параллельно. U3 лучше после U2 (чтобы patch contract_date через `atomic_compare_and_swap` было в scope U2).

## Risks & Call-outs

- **P0.3 диагностика в U2**: если после фикса PATCH-мутаций активация всё ещё не работает — читать `docker compose -f docker-compose.dev.yml logs backend --tail=50` при клике на кнопку, прежде чем менять код.
- **SDK regen порядок**: `make generate-sdk` запускать строго после финального `make migrate` и перезапуска backend (OpenAPI схема рендерится из runtime). Преждевременный regen создаст устаревший SDK.
- **Manager files.delete**: бизнес-решение — менеджеры должны иметь право удалять файлы (согласно requirements). Если политика изменится — деприоритизировать U4.
- **contract_date NOT NULL**: ни в коем случае не добавлять `NOT NULL` без DEFAULT в Sprint A (существующие записи → migration failure). Только `null=True, blank=True`.

## Overall Verification

```bash
# 1. Миграции
make migrate

# 2. SDK (только если изменялись DTO)
make generate-sdk

# 3. Полный тест-прогон (с запущенным Celery worker)
make test-all   # → 851+ passed

# 4. Линтинг
docker compose -f docker-compose.dev.yml exec backend ruff check . --fix
docker compose -f docker-compose.dev.yml exec backend ruff format .

# 5. Frontend build
docker compose -f docker-compose.dev.yml exec frontend npm run build
```

Manual checklist:
- [ ] PATCH `/api/v1/contragents/{id}/` → 200
- [ ] PATCH `/api/v1/contracts/{id}/` (DRAFT) → 200
- [ ] POST `/api/v1/contracts/{id}/activate/` → 200, status=ACTIVE
- [ ] `contract_date` видна в create/edit формах и в карточке
- [ ] Кнопка "Удалить" видна в `/dashboard/files` для manager; DELETE → файл исчезает
- [ ] "Экспорт в Excel" в reports → скачивается `.xlsx`
