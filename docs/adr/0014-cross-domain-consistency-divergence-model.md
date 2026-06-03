# ADR-0014: Cross-Domain Consistency Divergence Model & Resolution Policy

**Date:** 2026-06-03
**Status:** Proposed
**Author(s):** Platform / Common Infrastructure
**Deciders:** Platform Team

---

## Context

После ADR-0010–0013 транспортный слой гарантирует at-least-once delivery, lease recovery,
event ordering и bounded retention. Это **transport correctness** — но не **business consistency**.

Критический gap: два домена могут иметь взаимозависимые состояния без общей транзакции.
`ContractClosedEvent` обрабатывается Domain A (Contracts), порождает `FinanceTransactionPosted`
через Outbox в Domain B (Finance). Если Finance consumer упал за пределами retention window
или был stuck (Gap B из ADR-0013) — domains diverge: контракт юридически закрыт, проводки нет.

Это не transport failure — это **business consistency failure**. Существующие ADR описывают
delivery mechanics; ни один не отвечает на вопрос: _что значит расхождение_, _кто прав при
конфликте_, _как repair не нарушает downstream инварианты_.

Ключевое наблюдение: расхождение между доменами не симметрично. У Finance и Contracts разные
семантики авторитетности, разные типы инвариантов и разные окна исправимости. Попытка ввести
"глобальный reconciler" игнорирует эту асимметрию и порождает либо ложные позитивы (false
rollback), либо тихую потерю данных (silent overwrite).

**Key constraints:**

- Finance и Contracts не имеют общей транзакции: нет единой точки истины для cross-domain state.
- Finance — append-only ledger: обратная мутация (откат транзакции) нарушает бухгалтерский
  инвариант. Корректировка только через compensating entry.
- Contracts — state machine: `CLOSED → OPEN` автоматический откат недопустим без юридического
  обоснования.
- Downstream системы (billing, reporting, payout) могут уже потребить diverged state — их
  состояние не контролируется этим ADR.
- Repair для Finance domain требует human sign-off (ADR-0013, rule 4.7).

---

## Options Considered

### Option 1: Clock-based reconciliation scanner

**Description:** Background job периодически сравнивает Finance state с Contract state. При
обнаружении расхождения старше threshold T — помечает как diverged и инициирует repair.

**Pros:**
- Не требует изменений в event flow.
- Реализуется как standalone Celery task.

**Cons:**
- Clock skew, Celery retry jitter, outbox batching создают легитимные окна временного
  расхождения длиной до нескольких секунд. Scanner не может отличить "ещё не обработано"
  от "не будет обработано никогда" — гарантированные false positives.
- Trigger-ом служит time delta, а не причинно-следственная связь: scanner не знает,
  _ожидается_ ли вообще Finance event для данного Contract state.
- Масштабируется плохо: O(contracts × финансовые периоды) per scan.

**Why ruled out:** Clock-based detection даёт только observability signal (SLO alerting,
monitoring), но не является надёжным trigger для repair action.

---

### Option 2: Global authority resolver

**Description:** Один домен объявляется master of truth. При конфликте его состояние побеждает
и синхронизируется в другой домен.

**Pros:**
- Простая decision rule: один победитель всегда.

**Cons:**
- Finance и Contracts имеют разные типы инвариантов с несовместимой авторитетностью.
  Finance не может быть авторитетен для юридического статуса контракта. Contracts не может
  быть авторитетен для денежного движения.
- "Победа" одного домена означает молчаливое уничтожение другого: либо откат бухгалтерской
  проводки, либо откат юридического статуса — оба действия недопустимы без business approval.
- Не масштабируется на N доменов: при добавлении Reporting, Inventory, CRM потребуется
  N-factorial матрица авторитетности.

**Why ruled out:** Глобальный resolver — ложная абстракция. Авторитетность является свойством
инварианта, а не домена в целом.

---

### Option 3 (выбрано): Causal Divergence Detection + Domain-Scoped Compensating Resolution

**Description:** Расхождение определяется _каузально_: через нарушение ожидаемой причинно-
следственной пары событий (Contract event → Finance event). Разрешение — компенсирующее:
недостающая сторона пары создаётся как новое действие, авторитетная сторона никогда не
мутируется назад. Авторитетность разрешается per-invariant, не globally. Вводятся три класса
divergence с разными resolution policies.

**Pros:**
- Нет false positives от clock skew: divergence определяется через отсутствие causal pair,
  не через time delta.
- Авторитетность не нарушается: Finance ledger остаётся append-only, Contract state machine
  не откатывается.
- Три класса divergence дают разные escalation пути: Detectable → auto-repair, Observable →
  alerting, Irreversible → audit record.

**Cons:**
- Требует явного causal pair registry: разработчик должен декларировать ожидаемые пары
  при добавлении новых cross-domain flows.
- Compensating entry в Finance требует human sign-off — это operational overhead.
- Не покрывает случай, когда обе стороны пары доставлены, но бизнес-эффект неверен
  (это область domain-level validation, вне scope этого ADR).

---

## Decision

**Реализуем Causal Divergence Detection + Domain-Scoped Compensating Resolution (Option 3).**

Расхождение — это каузальное понятие: система diverged тогда и только тогда, когда
ожидаемая causal pair событий не замкнута в пределах reconciliation window. Авторитетность
не глобальна — она определяется типом инварианта: Finance authoritative для денежного
движения и баланса, Contracts authoritative для lifecycle и юридического статуса.
Reconciliation — всегда компенсирующее дополнение, никогда — откат назад во времени.

**Reconciliation Window (явно):** минимум из `OutboxRecord retention window` (ADR-0013) и
`Business finalization boundary` (закрытие учётного периода, финализация invoice batch,
выполнение payout settlement). За пределами window — divergence irreversible, фиксируется
только как audit record.

---

## Consequences

### Positive Consequences

- Ложные срабатывания repair исключены: trigger строго каузальный.
- Finance ledger сохраняет append-only semantics: compensating entry, а не retroactive mutation.
- Границы авторитетности задокументированы: при добавлении нового домена понятно, как
  определить его authority scope.
- Irreversible divergence формально признаётся как отдельный класс: нет попыток
  "починить непочинимое" автоматически.

### Negative Consequences / Accepted Tradeoffs

- **Causal pair registry** — новая операционная зависимость. При добавлении cross-domain
  flow разработчик обязан задекларировать pair. Недекларированные flows не детектируются.
- **Human sign-off для Finance repair** — operational overhead. Это осознанный трейдофф:
  автоматическое создание компенсирующей проводки без верификации хуже, чем задержка.
- **Clock-based monitoring остаётся только observability**: SLO нарушение по времени не
  является trigger для repair — только для alerting. Это может создать задержку реакции.
- Reporting, Billing и другие read-only downstream домены не покрываются этим ADR:
  их consistency — ответственность их domain layer.

### Risks

- Causal pair registry не обновлён при рефакторинге event flow → silent blind spot.
  Митигация: pair registry декларируется в коде (не конфиг), contract-тест verifies каждую пару.
- Compensating Finance entry создана дважды (оператор нажал replay дважды): duplicate posting.
  Митигация: `external_ref = f"repair:{original_outbox_record_id}"` + unique constraint
  (уже существует: `finance_tx_external_ref_uq`).
- Business finalization boundary не задекларирована для нового типа flow → repair выполнен
  за пределами допустимого окна → downstream inconsistency.
  Митигация: `CausalPairDefinition` содержит обязательное поле `finalization_boundary_source`.

---

## Implementation Notes

### 1. Таксономия divergence

Три класса, определяющие resolution strategy:

```text
Класс 1: Detectable Divergence
  Условие: causal pair не замкнута, OutboxRecord в retention window
  Resolution: compensating repair (auto или human-initiated)
  Пример: ContractClosed → FinanceTransactionPosted не доставлен

Класс 2: Observable Divergence
  Условие: causal pair не замкнута, clock > expected_latency SLO,
           OutboxRecord ещё в retention window или недостаточно данных
  Resolution: alerting только; repair по запросу оператора
  Пример: Finance state не совпадает с ожидаемым, но OutboxRecord ещё processing

Класс 3: Irreversible Divergence
  Условие: reconciliation window истёк (retention или business finalization)
  Resolution: audit record only; no auto-repair
  Пример: accounting period closed; balance already used in payout
```

### 2. Domain Authority Matrix

| Инвариант | Authoritative domain | Resolution rule при конфликте |
|---|---|---|
| Contract lifecycle (open/close/status) | Contracts | Never auto-revert. Correction только через explicit repair workflow. |
| Money movement (transaction posting) | Finance | Append compensating entry. Never delete/modify existing. |
| Balance correctness | Finance | Recompute from ledger (SELECT ... FOR UPDATE, SERIALIZABLE). |
| Invoice status | Finance | Authoritative after issuance. Contracts может сигнализировать, не мутировать. |
| Legal hold state | Contracts | Блокирует любую Finance mutation пока hold активен (rule 5.6). |

**Правило при неопределённой авторитетности:**

Если для нового cross-domain flow authority не задекларирована — требуется явный ADR
(или дополнение к этому ADR). Имплементация без задекларированной авторитетности запрещена.

### 3. Causal Pair Registry

Каждый cross-domain flow регистрирует ожидаемую causal pair:

```python
@dataclass(slots=True, frozen=True)
class CausalPairDefinition:
    name: str                              # "contract_closed → finance_posted"
    source_event: str                      # "contracts.ContractClosed"
    expected_event: str                    # "finance.TransactionPosted"
    correlation_field: str                 # "contract_id" / "outbox_record_id"
    expected_within_seconds: int           # SLO для closure (alerting trigger)
    finalization_boundary_source: str      # "finance.AccountingPeriod.is_closed"
    authority_on_conflict: str             # "finance" | "contracts" | "require_human"
    repair_requires_sign_off: bool         # True для всех Finance flows

CAUSAL_PAIRS: tuple[CausalPairDefinition, ...] = (
    CausalPairDefinition(
        name="contract_closed → finance_transaction_posted",
        source_event="contracts.ContractClosed",
        expected_event="finance.TransactionPosted",
        correlation_field="contract_id",
        expected_within_seconds=300,
        finalization_boundary_source="finance.AccountingPeriod.is_closed",
        authority_on_conflict="require_human",
        repair_requires_sign_off=True,
    ),
)
```

### 4. Reconciliation Window — формальное определение

```text
ReconciliationWindow(flow) = min(
    OutboxRetentionWindow,              # ADR-0013: OUTBOX_ARCHIVE_WINDOW_DAYS
    BusinessFinalizationBoundary(flow)  # задекларирован в CausalPairDefinition
)

За пределами окна:
  → divergence_class = IRREVERSIBLE
  → action = audit_record_only
  → auto_repair = forbidden
```

`BusinessFinalizationBoundary` — это не константа. Для Finance это состояние объекта
(AccountingPeriod.is_closed, InvoiceBatch.finalized_at). Проверка выполняется
**синхронно** перед инициацией repair, не по расписанию.

### 5. Compensating Resolution Flow

Полный flow для Класса 1 (Detectable Divergence):

```text
1. Causal pair monitor обнаруживает незамкнутую пару:
   ContractClosed(contract_id=X) без FinanceTransactionPosted в пределах expected_within_seconds

2. Проверка ReconciliationWindow:
   a. OutboxRecord для ContractClosed → retention window? → продолжить
   b. AccountingPeriod для contract → is_closed? → если да, escalate IRREVERSIBLE

3. Классификация:
   если в пределах SLO → Observable → alerting, пока не истечёт SLO
   если за SLO и в retention window → Detectable → инициировать repair

4. Repair workflow (Finance):
   a. оператор получает alert с deduplication_key = f"repair:contract_closed:{X}"
   b. оператор верифицирует: проводка действительно отсутствует
   c. вызов ConsumerRepairService.reset_for_repair() (ADR-0013)
   d. повторный dispatch через DLQ (ADR-0013)
   e. Finance consumer создаёт TransactionPosted с external_ref = deduplication_key
   f. unique constraint предотвращает дублирование
   g. оператор: AdminDlqManagementService.mark_repaired()

5. Audit trail (обязателен для Finance):
   CausalPairRepairRecord(
       source_event_id, expected_event_type, repair_initiated_by,
       repair_completed_at, compensating_transaction_id, sign_off_by
   )
```

### 6. Диагностика без repair (clock-based monitoring)

Clock-based reconciliation используется **только** как observability signal:

```python
# Celery beat task — monitoring queue, НЕ repair
@app.task(base=BaseEnterpriseTask, queue="notifications")
def check_causal_pair_slo_task() -> None:
    for pair_def in CAUSAL_PAIRS:
        threshold = tz.now() - timedelta(seconds=pair_def.expected_within_seconds)
        # Найти source events старше SLO без соответствующего expected event
        # Emit alert, НЕ инициировать repair
        # Логировать как divergence_class=OBSERVABLE
```

Этот task — только для alerting. Repair инициируется только оператором или через DLQ.

### 7. Contract-тест для causal pair registry

Каждая `CausalPairDefinition` должна иметь contract-тест:

```python
# apps/common/tests/contract/test_causal_pairs.py
@pytest.mark.parametrize("pair_def", CAUSAL_PAIRS)
def test_causal_pair_events_exist(pair_def: CausalPairDefinition) -> None:
    """Verify that source and expected event types are still registered."""
    assert pair_def.source_event in EVENT_REGISTRY
    assert pair_def.expected_event in EVENT_REGISTRY
    assert pair_def.finalization_boundary_source is not None
```

Тест падает при рефакторинге event names → blind spot невозможен.

### 8. Что НЕ покрывается этим ADR

- **Reporting domain**: read-only проекция. Eventual consistency. Не требует repair.
- **Bidirectional conflicts** (оба домена изменились независимо за период недоступности):
  требуют отдельного ADR с vector clock или CRDT semantics — вне scope ERP.
- **Domain-level validation failures** (событие доставлено, но business rule нарушена внутри
  домена): это domain layer ответственность, не cross-domain consistency.
- **Notification domain**: fire-and-forget, no consistency guarantee required.

---

## Review Date

Пересмотреть при:
- Добавлении нового cross-domain flow без существующей causal pair definition (обязательно).
- P99 repair latency превышает `expected_within_seconds` для любой пары — SLO недостижим.
- Запросе audit на период за пределами retention window → требуется отдельный event log ADR.
- Масштабировании до N > 5 cross-domain flows → causal pair registry требует versioning.

---

## Связанные ADR

- [ADR-0010](0010-transactional-outbox-delivery-guarantees.md) — delivery guarantees; этот ADR определяет что происходит когда delivery правильна, но business consistency нарушена
- [ADR-0011](0011-consumer-lease-reclaim-protocol.md) — transport recovery; этот ADR — над-транспортный слой
- [ADR-0012](0012-hybrid-event-ordering-strategy.md) — ordering semantics; Mode A (Finance) совместим с compensating resolution
- [ADR-0013](0013-outbox-retention-and-dlq-repair.md) — RetentionWindow и ConsumerRepairService используются в repair flow
- [ADR-0005](0005-bounded-context-ownership.md) — domain authority границы подтверждены этим ADR
- [ADR-0002](0002-zero-downtime-migration-governance.md) — Expand & Contract для `CausalPairRepairRecord` таблицы
