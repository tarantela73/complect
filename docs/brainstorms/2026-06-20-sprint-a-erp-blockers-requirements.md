---
title: "Sprint A — Исправление блокеров ERP"
date: "2026-06-20"
status: active
priority: P0
domains:
  - contragents
  - contracts
  - files
  - reports
sprint: A
---

# Sprint A — Исправление блокеров ERP

## Контекст

Шесть P0-дефектов блокируют реальную работу в ERP. Без них система фактически read-only:
контрагентов и договоры нельзя вести, файлы нельзя удалять, отчёты нельзя выгружать.

## Цели

- Разблокировать мутирующие операции: обновление, активация (контрагенты, договоры)
- Добавить обязательное поле `contract_date` конец-в-конец
- Включить удаление файлов в модуле Files
- Восстановить Excel-экспорт в отчётах (Auth bug)

## Бизнес-правила и ограничения

- Все мутирующие операции проходят через RBAC (`require_permission`). Если пользователь не имеет нужной роли — операция недоступна, это ожидаемое поведение, а не баг.
- `contract_date` — дата подписания договора. Обязательна к отображению, но при создании может быть пустой (nullable) для совместимости с существующими записями (Expand & Contract rule).
- Удаление файла — soft delete с подтверждением в UI. Hard delete запрещён.
- Excel-экспорт — скачивание с авторизацией; данные фильтруются по тенанту пользователя.

---

## P0.1 — Обновление контрагента (PATCH)

**Текущее поведение:** форма редактирования контрагента (`/dashboard/contragents/{id}/edit`) возвращает "Ошибка обновления" при сохранении.

**Ожидаемое поведение:** `PATCH /api/v1/contragents/{id}/` с `{ name }` возвращает 200 с обновлённым объектом; пользователь перенаправляется на `/dashboard/contragents/{id}`.

**Первый шаг диагностики:** проверить реальный HTTP-статус и `error.code` в ответе сервера.

- `403 PERMISSION_DENIED` → не назначена роль `contragents.write` для данного пользователя
- `5xx` → code bug в `apps/contragents/api.py:update_contragent` или `get_contragent_detail`

**Acceptance criteria:**
- `PATCH /api/v1/contragents/{id}/` с валидным `{ name }` возвращает 200 для пользователя с `contragents.write`
- Ответ — корректный `ContragentResponse` (name, inn, contragent_type, id)
- Фронтенд после успешного сохранения переходит на карточку контрагента
- Ошибочный сценарий (юридическое удержание) → 422 с корректным `error.code`

---

## P0.2 — Обновление договора (PATCH)

**Текущее поведение:** форма редактирования договора (`/dashboard/contracts/{id}/edit`) возвращает "Ошибка обновления".

**Ожидаемое поведение:** `PATCH /api/v1/contracts/{id}/` с `{ title?, total_amount? }` для DRAFT-договора возвращает 200.

**Первый шаг диагностики:** аналогично P0.1 — HTTP-статус из сервера.

- Договор не в DRAFT → UI уже показывает блокирующий текст, не форму (правильное поведение)
- `403` → RBAC `contracts.write` не назначена
- `5xx` → bug в `apps/contracts/api.py:update_contract` или `ContractRepository.atomic_compare_and_swap`

**Acceptance criteria:**
- `PATCH /api/v1/contracts/{id}/` для DRAFT-договора возвращает 200 с обновлёнными полями
- OCC-конфликт → 409 с `OCC_CONFLICT`
- Не-DRAFT статус → UI блокирует форму (уже реализовано — не регрессировать)

---

## P0.3 — Активация черновика договора (DRAFT → ACTIVE)

**Текущее поведение:** кнопка "Активировать" в карточке договора не выполняет переход.

**Ожидаемое поведение:** `POST /api/v1/contracts/{id}/activate/` переводит договор из DRAFT в ACTIVE, создаёт `ContractActivatedEvent` в Outbox, возвращает 200 с обновлённым объектом.

**Первый шаг диагностики:** проверить response status при клике. Вероятные причины:

- `403` → RBAC `contracts.write` не назначена
- `422 BUSINESS_RULE_VIOLATION` → договор уже не в DRAFT
- `5xx` → bug в `ContractActivateService` или контекстном тенанте

**Acceptance criteria:**
- Успешная активация → кнопка "Активировать" исчезает, статус меняется на ACTIVE
- Повторная попытка активации ACTIVE-договора → 422 с понятным сообщением
- Outbox событие `CONTRACT_ACTIVATED` создаётся в той же транзакции

---

## P0.4 — Дата договора (`contract_date`)

**Текущее поведение:** модель `Contract`, DTO и UI не содержат поля даты договора. Поле отсутствует на всех экранах.

**Ожидаемое поведение:** дата договора видна в форме создания, редактирования и на карточке договора.

**Бизнес-правило:**
- Поле называется `contract_date` (дата подписания/заключения договора)
- Тип: `date` (без времени)
- При создании — необязательное (nullable); при редактировании DRAFT — редактируемое
- Отображается в формате `DD.MM.YYYY` в UI

**Expand & Contract (ADR-0002):**
- Release N (Sprint A): nullable `DateField`, без `NOT NULL`
- Release N+1: backfill существующих записей (опционально — поле опциональное)

**Acceptance criteria:**
- Миграция добавляет `contract_date DATE NULL` без `NOT NULL` без индекса (поле для отображения, не для поиска)
- `ContractCreateRequestSchema` принимает `contract_date: Optional[date]`
- `ContractUpdateSchema` принимает `contract_date: Optional[date]`
- `ContractResponseSchema` и `ContractDetailSchema` возвращают `contract_date`
- Форма создания (`/dashboard/contracts/create`) — поле выбора даты (date input)
- Форма редактирования (`/dashboard/contracts/{id}/edit`) — поле редактирования даты
- Карточка договора (`/dashboard/contracts/{id}`) — строка "Дата договора: DD.MM.YYYY"
- Generated TS SDK пересоздан после изменения OpenAPI-схемы

---

## P0.5 — Удаление файла в модуле Files

**Текущее поведение:** кнопка "Удалить" в `/dashboard/files` и `/dashboard/files/{id}` не работает.

**Ожидаемое поведение:** `DELETE /api/v1/files/{id}` выполняет soft delete (устанавливает `is_deleted=True`); файл исчезает из списка.

**Backend endpoint:** `apps/files/api/files_api.py:88` — существует, реализует soft delete через `FileRepository.soft_delete`.

**Первый шаг диагностики:** `useCan('files.delete')` → если `false`, кнопка не отображается. Проверить назначение `files.delete` пользователю.

**UI требования:**
- Кнопка "Удалить" видна только при `files.delete` разрешении
- Клик → диалог подтверждения: "Удалить файл «{filename}»? Это действие нельзя отменить."
- Подтверждение → DELETE-запрос → файл исчезает из списка
- Ошибка удаления (legal hold / файл не найден) → toast с текстом из `error.message`

**Acceptance criteria:**
- `DELETE /api/v1/files/{id}` для `AVAILABLE` файла возвращает 204
- `DELETE /api/v1/files/{id}` для файла с `is_legal_hold=True` → 422 `LEGAL_HOLD`
- Удалённый файл не отображается в списке (инвалидация `filesKeys.lists()`)

---

## P0.6 — Excel экспорт отчётов

**Текущее поведение:** кнопки "Экспорт в Excel" на страницах договоров, финансов и банковских выписок не скачивают файл.

**Root cause:** `downloadReport(url)` в `frontend/src/shared/api/reports.ts` открывает URL через `<a href>` без Authorization header. Endpoints с `auth=None` в Ninja-роутере не получают JWT → `jwt_tenant_id=None` → `require_permission` → PermissionDenied → 403.

**Ожидаемое поведение:** Excel-файл скачивается с данными, отфильтрованными по тенанту пользователя.

**Решение (паттерн `downloadPdf`):** заменить `downloadReport` на `fetch` + blob с `Authorization: Bearer <token>` header. Token получать из той же сессии (существующий механизм хранения JWT в frontend).

**Acceptance criteria:**
- Кнопка "Экспорт в Excel" в `/dashboard/reports/contracts` скачивает `.xlsx` файл
- Кнопка "Экспорт в Excel" в `/dashboard/reports/finance` скачивает `.xlsx` файл
- Кнопка "Экспорт в Excel" в `/dashboard/reports/payments` скачивает `.xlsx` файл
- Незалогиненный пользователь → 401/403 (не файл)
- Пользователь без нужного пермишена → 403

---

## Out of Scope (Sprint B / C)

| Пункт | Sprint | Причина отсрочки |
|-------|--------|------------------|
| Удаление контрагента | B | Нужно определить политику: "есть активные договоры → запрет" требует cross-domain проверки |
| Контрагент 360° (договоры, объекты) | B | Связи + cross-domain queries — Sprint B |
| Прикрепление файлов к контрагенту | B | Нет FileAttachment для contragents-домена |
| Закрытие через именованные типы документов | C | Backend блокирует при отсутствии любых файлов; Sprint C — UX + типы |
| Поиск при привязке объекта (async select) | B | Sprint B |
| Объект 360° (контрагент) | B | Sprint B |
| Уведомления: очистка | C | Sprint C |

---

## Зависимости

- **P0.4**: требует `make makemigrations && make migrate`; после изменения DTO — регенерация TS SDK (`make generate-sdk`)
- **P0.1, P0.2, P0.3, P0.5**: диагностика логов (`docker compose logs backend --tail=100`) до написания кода
- **P0.6**: нужен механизм получения JWT-токена в frontend для `fetch` (проверить существующий hook `useSession` или аналог)

---

## Критерии готовности Sprint A

- [ ] Контрагент PATCH возвращает 200 для пользователя с `contragents.write`
- [ ] Договор PATCH возвращает 200 для DRAFT-договора у пользователя с `contracts.write`
- [ ] Активация DRAFT-договора меняет статус на ACTIVE
- [ ] `contract_date` присутствует в create/edit формах и в карточке договора
- [ ] Файл удаляется (soft delete) с подтверждением; кнопка видна при наличии права
- [ ] Все три Excel-экспорта (contracts, finance, payments) скачивают файлы
- [ ] `make test-all` проходит (851+ passed) с работающим Celery worker
- [ ] TS SDK пересоздан если изменились OpenAPI-схемы
