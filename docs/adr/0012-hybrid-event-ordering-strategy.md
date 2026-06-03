# ADR-0012: Hybrid Event Ordering Strategy for Transactional Outbox

**Date:** 2026-06-03
**Status:** Proposed
**Author(s):** Platform / Common Infrastructure
**Deciders:** Platform Team

---

## Context

После ADR-0010 (Transactional Outbox) и ADR-0011 (Lease & Reclaim Protocol) транспортный слой
гарантирует at-least-once delivery и автоматическое восстановление после сбоя consumer'а.
Оставшийся архитектурный gap: **event ordering**. Outbox dispatcher запускает события через
Celery — параллельные воркеры могут получить и обработать события одного агрегата в произвольном
порядке. Если `ContractUpdated(v3)` достигает consumer'а раньше `ContractUpdated(v2)`, и consumer
применяет последнее полученное состояние — v2 перезапишет v3. Это регрессия состояния данных.

Критически важно, что требование к упорядоченности **не универсально**: оно является свойством
типа события, а не свойством системы в целом. Delta-события (финансовые проводки, операции
изменения составных записей) требуют строгого FIFO — пропуск или перестановка означает потерю
информации. Snapshot-события (статус контракта, последнее значение поля) допускают монотонное
продвижение — достаточно не применять состояние старее уже обработанного.

Попытка ввести глобальный строгий FIFO по всему outbox избыточна, дорога и создаёт узкое место
без бизнес-обоснования для большинства доменов.

**Key constraints:**

- Не ломать существующих consumers: изменения добавляются, не заменяют текущий API.
- Нет зависимости от внешних систем упорядоченности (Kafka, Kinesis): PostgreSQL + Redis достаточны.
- Ordering enforcement не должен вводить глобальный bottleneck на всю систему.
- Finance domain: транзакционные delta-события обязаны обрабатываться строго по порядку.
- Snapshot domains: eventual consistency с монотонным прогрессом достаточна.

---

## Options Considered

### Option 1: Глобальный FIFO через единственную очередь

**Description:** Все outbox события обрабатываются одной очередью с единственным воркером.
Строгий FIFO гарантирован физически.

**Pros:**
- Максимально простая реализация ordering.
- Нет логики выбора стратегии.

**Cons:**
- Единственный воркер — throughput bottleneck для всей системы.
- Один медленный consumer блокирует обработку несвязанных агрегатов.
- Горизонтальное масштабирование невозможно.

**Why ruled out:** Несоразмерное ограничение. Агрегаты разных tenant'ов не имеют семантической
зависимости — нет основания сериализировать их обработку.

---

### Option 2: Строгий FIFO с per-aggregate буферизацией на consumer

**Description:** Consumer получает события в произвольном порядке, но буферизует их по
`aggregate_seq`. Обрабатывает только в правильном порядке. При получении seq=13 до seq=12 —
парковать 13, ждать 12.

**Pros:**
- Строгий FIFO без изменения dispatcher'а.
- Полностью на consumer стороне.

**Cons:**
- Сложность на каждом consumer: парковка, таймаут ожидания, fallback при потере события.
- Gap навсегда заблокирует очередь при потере события.
- Состояние парковки нужно хранить (in-memory = потеря при restart, in-DB = дополнительная схема).

**Why ruled out:** Перекладывает архитектурную ответственность на каждый consumer. Слишком сложно
и хрупко для случаев, где достаточна монотонная fence.

---

### Option 3: Монотонная Fence без serial dispatch (только `AggregateSequenceFence`)

**Description:** Consumer проверяет `last_seen_seq` перед обработкой. Если пришедший
`aggregate_seq ≤ last_seen_seq` — discard. Без гарантии порядка на стороне dispatcher'а.

**Pros:**
- Простой consumer-side guard.
- Нет изменений в dispatcher.

**Cons:**
- Gap при out-of-order доставке: seq=11 обработан, seq=13 обработан, seq=12 — discarded навсегда.
- Для delta-событий это потеря данных.
- Не решает проблему параллельного apply для Finance domain.

**Why ruled out:** Достаточна только для snapshot-событий. Для Finance (delta semantics) — опасна.

---

### Option 4 (выбрано): Hybrid Dual-Mode Ordering Strategy

**Description:** Система использует два режима в зависимости от семантики событий:

- **Mode A — Serial Dispatch**: dispatcher маршрутизирует события по `aggregate_id`. Все события
  одного агрегата попадают в одну очередь-слот и обрабатываются последовательно. Используется
  для доменов с delta-событиями (Finance).

- **Mode B — Monotonic Fence**: dispatcher не сериализирует delivery. Consumer читает
  `AggregateSequenceFence.last_seen_seq` и discards события старее уже обработанных. Используется
  для snapshot-доменов (Contracts, Contragents).

**Pros:**
- Finance получает строгий FIFO без глобального bottleneck.
- Snapshot-домены сохраняют параллелизм при минимальном overhead.
- Ordering enforced by construction (Serial Dispatch) — не requires reconciliation logic.
- Чёткое разделение: `aggregate_type` однозначно определяет режим.

**Cons:**
- Два режима требуют явного решения при добавлении нового event type.
- Вводятся три новых таблицы: `AggregateSequence`, `AggregateSequenceFence`, и поля в `OutboxRecord`.
- Serial Dispatch снижает параллелизм per-aggregate (допустимо: агрегаты независимы).

---

## Decision

**Реализуем Hybrid Dual-Mode Ordering Strategy (Option 4).**

Outbox dispatcher получает routing awareness по `aggregate_id`: события одного агрегата
направляются в одну logical queue (через routing key). Finance-домен (delta semantics) использует
Mode A: serial dispatch + per-aggregate serial consumption. Snapshot-домены используют Mode B:
параллельная доставка с `AggregateSequenceFence` guard на consumer. Выбор режима определяется
полем `ordering_mode` в `AggregateSequence` registry или константой в `DELTA_EVENT_TYPES` на
уровне dispatcher'а.

Ключевой принцип: **ordering by construction > ordering by reconciliation**. Mode A устраняет
саму возможность out-of-order для Finance, а не обнаруживает и исправляет её после факта.

**Точность гарантии Mode A.** Serial Dispatch даёт **per-slot single-flight processing** —
операционный инвариант, а не абсолютную гарантию строгого FIFO. FIFO выполняется при условии:
нет параллельных consumer'ов на одном слоте, нет speculative prefetch, нет broker redelivery
после worker crash. Эти условия обеспечиваются операционно (см. Slot Locking Invariant ниже),
не архитектурно. При нарушении любого из них Mode A деградирует до «best-effort FIFO».

---

## Consequences

### Positive Consequences

- Finance domain: строгий FIFO per-aggregate без distributed coordinator.
- Snapshot domains: горизонтальный масштаб сохранён, минимальный overhead на fence check.
- Новый event type получает явную классификацию при создании — нет «забытых» ordering требований.
- `aggregate_seq` в `OutboxRecord` даёт auditable порядок событий для любого агрегата.

### Negative Consequences / Accepted Tradeoffs

- **Serial Dispatch снижает throughput per-aggregate**: для одного Contract с интенсивными
  обновлениями задержка обработки = сумма processing time предыдущих событий в слоте.
  Приемлемо: типичный агрегат не генерирует events чаще 1/с.
- **Monotonic Fence допускает постоянный discard при gaps**: если seq=12 никогда не придёт
  (например, permanently failed outbox record), seq=12 будет discarded навсегда. Для snapshot
  это ок; это ещё раз подтверждает, что delta-домены должны использовать Mode A.
- Три новые таблицы и поля в `OutboxRecord` — миграции по ADR-0002 (Expand & Contract).
- Классификация события (`delta` vs `snapshot`) — ответственность domain developer'а при написании
  нового consumer. CI-guard не предусмотрен.

### Risks

- **Неверная классификация**: если delta-событие попадает в Mode B — данные теряются без
  ошибки. Это silent corruption, не crash. Митигация: явный список `SERIAL_DISPATCH_AGGREGATE_TYPES`
  в конфиге dispatcher'а, обязательный code review Finance domain, правило классификации
  (см. Event Classification Rules ниже).
- **Celery FIFO не абсолютен**: `prefetch_multiplier > 1`, redelivery после worker crash,
  broker visibility timeout — всё это нарушает порядок внутри слота. Митигация: `CELERY_WORKER_PREFETCH_MULTIPLIER = 1`
  (уже в CLAUDE.md rule 4.15), `acks_late = True`, `max_tasks_per_child = 1` для serial воркеров.
  Без этих настроек Mode A деградирует до best-effort FIFO.
- **Рост числа routing keys**: при большом числе агрегатов (>100K активных контрактов) количество
  Celery routing keys растёт. Митигация: hash-based sharding — `hash(aggregate_id) % N_SLOTS`
  вместо прямого routing по UUID. Заложить в реализацию dispatcher'а как параметр `SLOTS=256`.
- Пересматривается при: появлении delta-событий в snapshot-доменах (Contracts, Contragents).

---

## Implementation Notes

### Схема изменений

#### 1. `OutboxRecord` — новые поля

```python
# apps/common/models/outbox.py
aggregate_id   = models.UUIDField(null=True, blank=True, db_index=True)
aggregate_type = models.CharField(max_length=255, null=True, blank=True)
aggregate_seq  = models.BigIntegerField(null=True, blank=True)
```

Migrate: Release N — nullable. `NOT NULL` + GIN index — Release N+2 после backfill (ADR-0002).

#### 2. `AggregateSequence` — row-level monotonic allocator

```sql
CREATE TABLE common_aggregate_sequences (
    tenant_id      UUID        NOT NULL,
    aggregate_type VARCHAR(255) NOT NULL,
    aggregate_id   UUID        NOT NULL,
    current_seq    BIGINT      NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, aggregate_type, aggregate_id)
);
```

Allocation (внутри той же транзакции, что INSERT в outbox):

```sql
INSERT INTO common_aggregate_sequences (tenant_id, aggregate_type, aggregate_id, current_seq)
VALUES (%s, %s, %s, 1)
ON CONFLICT (tenant_id, aggregate_type, aggregate_id)
DO UPDATE SET current_seq = common_aggregate_sequences.current_seq + 1
RETURNING current_seq;
```

Row-level lock via `ON CONFLICT DO UPDATE` — O(1), без `MAX()`, без сканирования outbox.
Rollback транзакции бизнес-операции откатывает и allocator UPDATE — gap невозможен.

#### 3. `AggregateSequenceFence` — Mode B guard

```sql
CREATE TABLE common_aggregate_sequence_fences (
    tenant_id      UUID        NOT NULL,
    aggregate_type VARCHAR(255) NOT NULL,
    aggregate_id   UUID        NOT NULL,
    consumer       VARCHAR(255) NOT NULL,
    last_seen_seq  BIGINT      NOT NULL DEFAULT 0,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, aggregate_type, aggregate_id, consumer)
);
```

Consumer-side check (Mode B):

```python
def should_process(tenant_id, aggregate_type, aggregate_id, consumer, event_seq) -> bool:
    updated = AggregateSequenceFence.objects.filter(
        tenant_id=tenant_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        consumer=consumer,
        last_seen_seq__lt=event_seq,
    ).update(last_seen_seq=event_seq, updated_at=now())
    if not updated:
        # Либо запись не существует (первое событие), либо event_seq уже обработан/устарел.
        # Проверяем: если запись отсутствует — создаём.
        _, created = AggregateSequenceFence.objects.get_or_create(
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            consumer=consumer,
            defaults={"last_seen_seq": event_seq},
        )
        return created  # True = первое событие; False = устаревшее
    return True
```

Атомарный `UPDATE WHERE last_seen_seq < event_seq`: два конкурирующих воркера — первый UPDATE
захватывает строку; второй находит `last_seen_seq ≥ event_seq` → 0 rows → discard.

#### 4. Dispatcher routing (Mode A)

```python
# apps/common/tasks/outbox_dispatcher.py
SERIAL_DISPATCH_AGGREGATE_TYPES = frozenset({
    "finance.transaction",
    "finance.balance",
})

N_ROUTING_SLOTS = 256  # hash sharding против взрыва числа routing keys

def _routing_key(record: OutboxRecord) -> str:
    if record.aggregate_type in SERIAL_DISPATCH_AGGREGATE_TYPES:
        slot = int(record.aggregate_id) % N_ROUTING_SLOTS if record.aggregate_id else 0
        return f"outbox.serial.{slot}"
    return "outbox"
```

Celery worker для serial slots:

```python
# celery worker --queues=outbox.serial.0,...,outbox.serial.255 --concurrency=1
# (один воркер per slot → FIFO per slot)
```

Hash sharding: `N_ROUTING_SLOTS=256` → максимум 256 параллельных потоков для Finance,
каждый слот обрабатывается строго последовательно.

#### 5. Slot Locking Invariant (Mode A)

Mode A гарантирует FIFO только при соблюдении всех трёх условий:

```text
At any point in time, at most one active consumer processes a given slot.
```

Операционные требования для обеспечения инварианта:

| Параметр | Значение | Причина |
|---|---|---|
| `CELERY_WORKER_PREFETCH_MULTIPLIER` | `1` | нет спекулятивного prefetch |
| `acks_late` | `True` | ACK только после обработки |
| `max_tasks_per_child` | `1` | нет накопленного состояния между задачами |
| concurrency per serial worker | `1` | один поток на слот |

При нарушении любого из условий Mode A деградирует до best-effort FIFO.
Нарушение не вызывает ошибку — оно вызывает silent reordering.

### Event Classification Rules

**Это правило обязательно для любого нового event type:**

```text
IF событие мутирует состояние через delta (increment/decrement, append, remove)
   → MUST использовать Mode A (Serial Dispatch)

IF событие представляет последнее состояние (snapshot, last-write-wins)
   → MUST использовать Mode B (Monotonic Fence)

IF неизвестно или спорно
   → DEFAULT Mode A
```

**Почему default Mode A при неопределённости:** неверная классификация snapshot → Mode A
снижает параллелизм (принято). Неверная классификация delta → Mode B вызывает silent data
corruption без ошибок. Асимметрия рисков требует conservative default.

Примеры классификации:

| Event | Тип | Режим | Обоснование |
|---|---|---|---|
| `FinanceTransactionPosted` | delta | Mode A | проводка изменяет баланс |
| `BalanceRecalculated` | snapshot | Mode B | последнее значение перезаписывает предыдущее |
| `ContractStatusChanged` | snapshot | Mode B | статус = текущее состояние |
| `ContractUpdated` | snapshot* | Mode B | *если partial merge — пересмотреть |
| `LineItemAdded` | delta | Mode A | изменяет состав агрегата |
| `LineItemRemoved` | delta | Mode A | изменяет состав агрегата |

> **Предупреждение:** `ContractUpdated` может стать delta, если consumer делает partial field
> merge вместо полного overwrite. В этом случае требуется переклассификация в Mode A.

### Итоговая гарантийная матрица (обновление к ADR-0010)

| Сценарий | ADR-0010 | ADR-0011 | ADR-0012 |
|---|---|---|---|
| DB commit → dispatcher crash | ✅ | ✅ | ✅ |
| Redis duplicate delivery | ✅ | ✅ | ✅ |
| Consumer crash до ack | ⚠️ zombie | ✅ | ✅ |
| Zombie consumer | ⚠️ | ✅ | ✅ |
| Finance duplicate posting | ✅ | ✅ | ✅ |
| Out-of-order delivery (Finance) | ❌ | ❌ | ✅ Mode A |
| Out-of-order delivery (Snapshot) | ❌ | ❌ | ✅ Mode B |

### Связь с heartbeat() fix (ADR-0011 erratum)

В ходе проектирования ADR-0012 обнаружена race condition в `heartbeat()`: guard
`lease_expires_at__gt=now` отклоняет heartbeat, пришедший на 1ms позже expiry, в то время как
`reclaim()` другого воркера уже принял `lease_expires_at__lt=now`. Оба считают себя владельцами.

Фикс: grace-window + ownership check (уже присутствует как `claimed_by_instance_id`):

```python
grace = timedelta(seconds=ttl // 10)
...filter(
    claimed_by_instance_id=instance_id,  # ownership guard — reclaim меняет этот ключ
    lease_expires_at__gt=now - grace,
    acked_at__isnull=True,
)
```

После `reclaim()` owner меняется → старый `instance_id` не матчится даже внутри grace окна.
Двойного владельца нет. Применить к `ConsumerIdempotencyService.heartbeat()` одновременно с
реализацией ADR-0012.

---

## Review Date

Пересмотреть при:
- Появлении delta-семантики в Contracts или Contragents domain (перевести в Mode A).
- P99 обработки Finance events превышает 5s при `N_ROUTING_SLOTS=256` (увеличить slots или
  перейти на прямой routing по `hash(aggregate_id)`).
- Реализации Redis Streams как альтернативного transport (XGROUP per aggregate_id как
  нативный serial dispatch без Celery routing complexity).

---

## Связанные ADR

- [ADR-0010](0010-transactional-outbox-delivery-guarantees.md) — гарантийная матрица обновлена
- [ADR-0011](0011-consumer-lease-reclaim-protocol.md) — heartbeat() erratum применяется совместно
- [ADR-0002](0002-zero-downtime-migration-governance.md) — Expand & Contract для новых полей `OutboxRecord`
- [ADR-0005](0005-bounded-context-ownership.md) — domain ownership определяет классификацию event type
