# ADR-0021: Anti-IDOR Guard via Protocol Extension

**Date:** 2026-06-09
**Status:** Accepted
**Author(s):** sever
**Deciders:** Engineering Team

---

## Context

`ManualMatchService.attach()` принимает `transaction_id` из пользовательского ввода и привязывает строку выписки к указанной транзакции. Без верификации принадлежности транзакции тенанту — атака IDOR (Insecure Direct Object Reference): пользователь из тенанта A передаёт UUID транзакции тенанта B, получая кросс-доменную привязку. Стандартное решение — дополнительный SELECT по `(tenant_id, transaction_id)` — невозможно реализовать напрямую в `apps/payments`, поскольку ADR-0005 запрещает импорт моделей из `apps/finance` в другой домен. Нужен механизм проверки, не нарушающий границы bounded context.

**Key constraints:**
- ADR-0005: прямой `from apps.finance.models import Transaction` в `apps/payments` = ошибка CI
- Правило 5.x: 403 на отсутствующий/чужой ресурс должен возвращать нейтральный код ошибки (не раскрывать существование ресурса)
- Единственная легальная точка связи payments→finance: `apps/common/contracts/`

---

## Options Considered

### Option 1: Прямой ORM-запрос в payments service

**Description:** Добавить lazy import `from apps.finance.models import Transaction` внутри метода `attach()` с `# noqa: PLC0415`, выполнить `Transaction.objects.filter(id=..., tenant_id=...).exists()`.

**Pros:**
- Минимальный объём кода — одна строка проверки

**Cons:**
- Нарушает ADR-0005: прямой импорт модели из чужого домена
- Создаёт скрытую зависимость: изменение схемы `Transaction` может сломать `payments` без явной связи
- Нарушение будет пропущено линтером только при `noqa`, что маскирует проблему

**Why this was ruled out:** Нарушает архитектурный закон. `noqa: PLC0415` предназначен для lazy import самого Protocol/сервиса, не для прямого импорта моделей чужого домена.

---

### Option 2: Расширение FinanceTransactionFinder Protocol методом find_by_id (выбрано)

**Description:** Добавить метод `find_by_id(tenant_id, transaction_id) -> TxCandidateDTO | None` в Protocol `FinanceTransactionFinder` в `apps/common/contracts/finance_contracts.py`. Реализация — в `DjangoTransactionFinder` в `apps/finance`. Вызов — через `get_transaction_finder().find_by_id(...)` в `ManualMatchService.attach()`.

**Pros:**
- Не нарушает ADR-0005: payments видит только Protocol и DTO, не модели
- Tenant-скоп проверки находится в `_qs(tenant_id)` — единственном месте, что исключает drift
- Возвращает `None` вместо исключения — caller сам решает семантику (403 vs 404)
- Protocol становится полным контрактом безопасности, а не только поискового API

**Cons:**
- Расширение Protocol требует обновления всех реализаций (сейчас одна — `DjangoTransactionFinder`)
- `find_by_id` используется только для проверки, а не для получения данных — потенциально избыточный return type

---

### Option 3: Валидация на уровне API endpoint

**Description:** Выполнять проверку tenant в `attach_match()` view до вызова сервиса: отдельный запрос к БД через `BankStatement` или другой tenant-scoped ресурс.

**Pros:**
- Не требует изменения Protocol

**Cons:**
- Бизнес-инвариант (транзакция принадлежит тенанту) размещается в транспортном слое вместо service layer
- Дублирование логики при появлении второго endpoint или Celery-задачи, вызывающих `attach()`
- API-слой не имеет прямого доступа к модели Transaction без нарушения ADR-0005

**Why this was ruled out:** Смешение ответственностей. Проверка принадлежности ресурса — это инвариант сервисного слоя, не транспортного.

---

## Decision

**Расширяем `FinanceTransactionFinder` Protocol методом `find_by_id(tenant_id, transaction_id) -> TxCandidateDTO | None` и реализуем его в `DjangoTransactionFinder`.**

`ManualMatchService.attach()` вызывает `get_transaction_finder().find_by_id(tenant_id, transaction_id)` до UPDATE; при возврате `None` бросает `PermissionDenied("TRANSACTION_NOT_IN_TENANT")`. API endpoint перехватывает исключение и возвращает `403` с нейтральным кодом `TRANSACTION_NOT_FOUND` — не раскрывая, существует ли транзакция в другом тенанте.

---

## Consequences

### Positive Consequences
- IDOR полностью закрыт на уровне service layer — работает независимо от того, откуда вызывается `attach()`
- Protocol как контракт безопасности: любая новая реализация `FinanceTransactionFinder` обязана реализовать tenant-scoped `find_by_id`
- Нейтральный 403 не раскрывает существование UUID в чужом тенанте

### Negative Consequences / Accepted Tradeoffs
- Каждый вызов `attach()` выполняет дополнительный SELECT (`find_by_id`) перед UPDATE — +1 запрос к БД
- При появлении новой реализации Protocol (напр. mock для тестов) требуется реализовать `find_by_id`

### Risks
- Если `_qs(tenant_id)` в `DjangoTransactionFinder` изменит scope (напр. уберёт фильтр `is_deleted=False`), guard ослабнет — необходимо покрыть unit-тестами с явными assertions на tenant isolation

---

## Implementation Notes

- `find_by_id` переиспользует `_qs(tenant_id)` — тот же tenant-scoped QuerySet, что и в остальных методах finder
- Статусный фильтр `_MATCHABLE_STATUSES = (PENDING, POSTED)` применяется и в `find_by_id` — транзакция в статусе CANCELLED или DRAFT не может быть привязана
- Lazy import в `ManualMatchService`: `from apps.finance.services.transaction_finder import get_transaction_finder  # noqa: PLC0415`
- Тест-кейс обязателен: `attach()` с `transaction_id` из другого тенанта → `PermissionDenied`

---

## Review Date

Пересмотреть при: добавлении второй реализации `FinanceTransactionFinder` или переходе к event-driven проверке принадлежности (Saga).
