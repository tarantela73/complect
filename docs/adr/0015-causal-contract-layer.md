# ADR-0015: Causal Contract Layer — Minimal Semantic Kernel

**Date:** 2026-06-03
**Status:** Proposed
**Author(s):** Platform / Common Infrastructure
**Deciders:** Platform Team

---

## Context

ADR-0012 (ordering), ADR-0013 (recovery), ADR-0014 (divergence) каждый вводят каузальные
ссылки локально: `outbox_record_id` в DLQ, `external_ref = f"repair:{original_outbox_record_id}"`
в compensating entry, `correlation_id` в structlog. Ни один ADR не отвечает на вопрос:
**что является атомарной единицей причинности** и **какие поля должны сохраняться через все
слои — outbox → consumer → DLQ → repair — без потерь**.

Это создаёт практическую проблему: трассировка причинно-следственной цепочки требует
знания деталей каждого слоя. Разработчик, анализирующий инцидент через три месяца, должен
знать, что `external_ref` в Finance это `repair:{outbox_record_id}`, что `DeadLetterRecord`
хранит `outbox_record_id` в `payload`, что `AggregateSequenceFence` не содержит `correlation_id`.
Это имплицитное знание — оно нигде не зафиксировано как контракт.

**Key constraints:**

- Нельзя добавлять runtime-зависимость от внешней системы трассировки (no OpenTelemetry
  daemon в critical path).
- Контракт должен быть верифицируемым через существующие DB constraints и contract-тесты.
- Семантика `outbox_record_id` и `correlation_id` уже используется повсеместно — изменение
  их смысла ломает существующий код.

---

## Decision

**Вводим `CausalUnit` как минимальную стабильную единицу причинности** и три каузальных
инварианта, которым должны удовлетворять все слои системы.

`CausalUnit` — это не новая сущность в БД. Это набор полей, которые **совместно**
идентифицируют позицию события в причинно-следственном графе и которые ни один слой
не имеет права потерять или переопределить молча.

---

## Causal Graph — явная природа

**Causal graph в этой системе — логический граф, а не граф хранения (storage graph).**

Это разграничение критично: `causal_root_id` является логическим идентификатором, а не
внешним ключом. `OutboxRecord` с `id = causal_root_id` может быть архивирован или удалён
(ADR-0013 retention policy) — при этом `causal_root_id` в `DeadLetterRecord`, `ProcessedEvent`,
compensating `OutboxRecord` и Finance `external_ref` остаётся валидным как логическая ссылка.

**Последствия:**

| Вопрос | Ответ |
| --- | --- |
| Можно ли JOIN causal_root_id → OutboxRecord всегда? | **Нет**. После retention window OutboxRecord может отсутствовать. |
| Теряет ли causal_root_id смысл после архивации? | **Нет**. Он остаётся stable logical identifier для lineage reconstruction. |
| Где `causal_root_id` хранится дольше всего? | `ProcessedEvent` и Finance records (domain tables = permanent). |
| Что произойдёт при JOIN после архивации? | NULL / not found — не ошибка, а ожидаемое поведение. |

**Следствие для запросов:** causal lineage за пределами retention window реконструируема
только через поля, хранящиеся в domain tables: `correlation_id`, `causal_root_id` в
`ProcessedEvent` и `external_ref` в Finance transactions. Полный граф недоступен — только
leaf nodes и partial chains. Это принятый компромисс (система не event store).

**Следствие для Invariant 1 (sequence anchoring):** инвариант гарантирует только то, что
при архивации `OutboxRecord` не было активных Fence-ссылок на его `aggregate_seq`. Он не
запрещает `causal_root_id` ссылаться на уже архивированную запись — это нормально.

---

---

## CausalUnit — определение

```python
@dataclass(slots=True, frozen=True)
class CausalUnit:
    outbox_record_id: uuid.UUID   # transport-layer identity; уникален для каждого dispatch
    causal_root_id: uuid.UUID     # неизменный root причинной цепи; при repair = original outbox_record_id
    correlation_id: uuid.UUID     # cross-request trace identity; propagates from HTTP → Celery → downstream
    aggregate_id: str             # агрегат-источник события (ADR-0012)
    aggregate_type: str           # тип агрегата (ADR-0012)
    aggregate_seq: int            # порядковый номер в aggregate sequence (ADR-0012)
    tenant_id: uuid.UUID          # изоляция; не может изменяться между слоями
```

**Ключевое разграничение:** `outbox_record_id` ≠ `causal_root_id`:

- `outbox_record_id` — identity конкретного transport dispatch. При repair создаётся новый
  `OutboxRecord` → новый `outbox_record_id`.
- `causal_root_id` — root всей причинной цепи. При repair compensating event'а:
  `causal_root_id = оригинальный outbox_record_id ContractClosed`. Никогда не изменяется.

Это позволяет восстановить полную lineage: `ContractClosed → (repair) → FinanceTransactionPosted`
через единственный `causal_root_id`.

---

## Три каузальных инварианта

### Invariant 1: Sequence Anchoring

```text
Если aggregate_seq IS NOT NULL
→ соответствующий outbox_record_id ДОЛЖЕН существовать в OutboxRecord

Нарушение: AggregateSequenceFence.last_seen_seq ссылается на seq
           для которого OutboxRecord уже архивирован/удалён.
Митигация: OutboxRecord не удаляется пока AggregateSequenceFence
           referencing его aggregate содержит last_seen_seq >= его aggregate_seq.
```

### Invariant 2: DLQ Causal Preservation

```text
Если DLQ replay происходит
→ causal_root_id в репроцессированном событии ДОЛЖЕН совпадать с оригиналом

Нарушение: AdminDlqManagementService.execute_task_replay() пересоздаёт
           Celery task без propagation causal_root_id → новая цепь без root.
Митигация: execute_task_replay() копирует causal_root_id из оригинального
           payload без изменений. outbox_record_id в payload остаётся оригинальным.
```

### Invariant 3: Repair Root Immutability

```text
Если repair/compensation event создаётся
→ его causal_root_id ДОЛЖЕН равняться causal_root_id оригинального события,
  которое repair исправляет

Нарушение: compensating Finance entry создаётся с causal_root_id = новый UUID
           → разрыв причинной цепи. Finance lineage неполна.
Митигация: ConsumerRepairService.reset_for_repair() возвращает causal_root_id
           оригинала. Caller обязан использовать его при dispatch repair task.
           Compensating OutboxRecord содержит causal_root_id = оригинал.
```

---

## Таблица сохранения полей

Какие поля `CausalUnit` каждый слой обязан **принять на вход** и **передать без изменений**:

| Слой | outbox_record_id | causal_root_id | correlation_id | aggregate_seq | tenant_id |
|---|---|---|---|---|---|
| `TransactionalOutboxService.push_to_outbox()` | генерирует | = outbox_record_id (если нет) | propagates | allocates | propagates |
| `outbox_dispatcher.py` → Celery payload | propagates | propagates | propagates | propagates | propagates |
| `ConsumerIdempotencyService.claim()` | links | — (не хранит) | — (не хранит) | — (не хранит) | stores |
| `outbox_dispatcher.py` → DLQ xadd | propagates | propagates | propagates | propagates | propagates |
| `AdminDlqManagementService.execute_task_replay()` | propagates | **must propagate** | propagates | propagates | propagates |
| `ConsumerRepairService.reset_for_repair()` | returns | **returns original** | — | — | — |
| Compensating event (repair) | new | **= original causal_root_id** | propagates | new seq | propagates |

Ячейки **must propagate** — нарушение этих правил ломает Invariant 2 или 3.

---

## Верификация инвариантов

Инварианты верифицируются на двух уровнях:

**Уровень 1 — DB constraints (статическая):**

```sql
-- Invariant 1: sequence anchoring
-- При архивировании OutboxRecord: проверить что нет активных Fence с last_seen_seq
-- в диапазоне aggregate_seq этой записи.
-- Реализуется как pre-archive check в archive_processed_outbox_records_task (ADR-0013).
```

**Уровень 2 — contract-тесты (динамическая):**

```python
# apps/common/tests/contract/test_causal_invariants.py

def test_dlq_replay_preserves_causal_root_id():
    """Invariant 2: DLQ replay не изменяет causal_root_id."""
    ...

def test_repair_compensating_event_preserves_causal_root_id():
    """Invariant 3: compensating event содержит causal_root_id оригинала."""
    ...

def test_outbox_record_not_archived_while_fence_references_seq():
    """Invariant 1: archival blocked by active fence reference."""
    ...
```

---

## Что НЕ входит в этот ADR

- Визуализация causal graph, heatmaps, dashboards — ADR-0016 (pending).
- Causal query API (например, "дай все события с causal_root_id=X") — ADR-0016.
- Retention policy для `causal_root_id` после business finalization boundary — ADR-0013
  достаточен, если `causal_root_id` хранится в `OutboxRecord` как обычное поле.

---

## Implementation Notes

### Schema changes

```python
# OutboxRecord (ADR-0012 уже добавляет aggregate_id, aggregate_type, aggregate_seq)
# Добавить:
causal_root_id = models.UUIDField(db_index=True)
# default = self.id (при создании: causal_root_id = outbox_record_id если не repair)

# DeadLetterRecord (apps/common/models/dlq.py)
# Добавить:
causal_root_id = models.UUIDField(null=True, db_index=True)
# null=True: backward compat для записей до этого ADR
```

### push_to_outbox signature change

```python
def push_to_outbox(
    event: DomainEvent,
    *,
    causal_root_id: uuid.UUID | None = None,  # None → self.outbox_record_id
) -> OutboxRecord:
    ...
    record = OutboxRecord(
        ...
        causal_root_id=causal_root_id or new_id,
    )
```

`causal_root_id` передаётся только при repair flows. В normal flow — всегда `None`.

---

## Review Date

Пересмотреть при:
- Введении Event Sourcing: `causal_root_id` семантика потребует расширения для event streams.
- P99 инцидент где lineage восстановить невозможно несмотря на этот ADR: значит, пропущен
  слой, не попавший в таблицу сохранения полей.
- Реализации ADR-0016: causal query API должен опираться именно на `causal_root_id` + `aggregate_seq`.

---

## Связанные ADR

- [ADR-0012](0012-hybrid-event-ordering-strategy.md) — `aggregate_seq` является частью CausalUnit
- [ADR-0013](0013-outbox-retention-and-dlq-repair.md) — `ConsumerRepairService` обязан возвращать causal_root_id
- [ADR-0014](0014-cross-domain-consistency-divergence-model.md) — compensating events обязаны соблюдать Invariant 3
- ADR-0016 (pending) — Causal Observability Layer; опирается на CausalUnit как stable query atom
