# ADR-0016: Causal Query Semantics

**Date:** 2026-06-03
**Status:** Proposed
**Author(s):** Platform / Common Infrastructure
**Deciders:** Platform Team

---

## Context

ADR-0015 зафиксировал `CausalUnit` как стабильную единицу причинности и три инварианта,
которым должны удовлетворять все слои. После этого ADR система имеет:

- causal data — присутствует в `OutboxRecord`, `ProcessedEvent`, `DeadLetterRecord`, domain tables
- causal integrity — enforced через инварианты и contract-тесты

Отсутствует одно: **как задавать вопросы этой системе**.

Сейчас нет ответа на вопрос: "покажи мне всё, что произошло в системе из-за того, что
контракт X был закрыт" — кроме как через ручные SQL JOINы по четырём таблицам с разными
retention policy. Это делает postmortem, аудит и debugging принципиально ручными операциями.

Causal query surface — это не observability dashboard. Это формализация: **какие вопросы
о причинно-следственных цепях система обязана отвечать**, и **через какой слой это делается**
без нарушения инвариантов из ADR-0015.

**Key constraints:**

- Causal graph — логический, не storage (ADR-0015). Запрос должен работать даже когда
  часть цепи архивирована.
- Запросы к causal graph — read-only операции. Они не должны иметь side-effect на
  `OutboxRecord`, `ProcessedEvent` или domain tables.
- Finance causal queries требуют audit logging: кто, когда, зачем (rule 5.8).
- Causal query не является публичным API — это internal diagnostic surface.

---

## Options Considered

### Option 1: Materialized causal graph (отдельная таблица)

**Description:** Background job строит `CausalEdge(from_id, to_id, edge_type)` таблицу
из всех слоёв. Causal queries — это обычные graph traversal над этой таблицей.

**Pros:**
- Fast reads: граф уже построен.
- Независим от retention state source tables.

**Cons:**
- Requires continuous materialization: при каждом новом `OutboxRecord`, `ProcessedEvent`,
  DLQ record — обновление графа. Это eventual consistency над уже eventual consistent системой.
- Materialized graph сам требует retention policy.
- При нарушении инварианта в source (например, Gap B из ADR-0013) — graф содержит
  неверные рёбра до следующего rebuild.

**Why ruled out:** Два слоя eventual consistency без синхронизирующей транзакции — это
граф, которому нельзя доверять в постмортеме. Именно тогда, когда он нужен больше всего.

---

### Option 2: OpenTelemetry spans как causal backbone

**Description:** Все слои эмитят spans с `trace_id = correlation_id`. Causal query =
trace lookup по `correlation_id` в Jaeger/Tempo.

**Pros:**
- Стандартный tooling.
- Визуализация "из коробки".

**Cons:**
- OpenTelemetry collector — новый SPOF в critical path.
- `correlation_id` — не единственная ось причинности: `causal_root_id` не имеет прямого
  аналога в OTel модели.
- Spans не содержат `aggregate_seq` и `causal_root_id` — ADR-0015 инварианты не верифицируемы.
- Finance audit требует DB-уровня доказуемости, не external system.

**Why ruled out:** OTel — правильный инструмент для latency и tracing. Неправильный для
causal integrity verification и Finance audit. Два разных требования нельзя закрыть одним инструментом.

---

### Option 3 (выбрано): Causal Query Layer поверх существующих таблиц

**Description:** Тонкий query layer, который знает как соединить `OutboxRecord`,
`ProcessedEvent`, `DeadLetterRecord` и domain tables через `causal_root_id` /
`correlation_id` / `outbox_record_id`. Запросы работают over existing tables без
materialization. При архивации source records — graceful degradation (partial chain).

**Pros:**
- Нет новой инфраструктуры: только SELECT поверх существующих индексированных полей.
- Causal integrity верифицируема: запрос видит ровно то, что реально произошло.
- Partial chain — явный, не silent: запрос возвращает `chain_complete: bool`.
- Finance audit: все reads через этот layer логируются автоматически (rule 5.8).

**Cons:**
- Запрос может быть медленным при длинных цепях через несколько таблиц.
- После retention window chain неполна — caller обязан обработать `chain_complete=False`.

---

## Decision

**Реализуем Causal Query Layer поверх существующих таблиц (Option 3).**

Query layer — это read-only service, который отвечает на три класса вопросов
(Query Surface, см. ниже) через `causal_root_id` и `correlation_id` как primary axes.
Возвращает `CausalChain` — структуру с явным полем `chain_complete`, чтобы caller
всегда знал, полная цепь или нет.

---

## Consequences

### Positive Consequences

- Postmortem и аудит используют единый entry point, а не ручные JOINы.
- `chain_complete=False` — явная сигнализация архивации: нет тихой потери данных.
- Finance causal reads автоматически audit-logged через query layer.
- Causal graph integrity верифицируема: если `chain_complete=True`, значит все
  три инварианта ADR-0015 выполнены для этой цепи в пределах retention window.

### Negative Consequences / Accepted Tradeoffs

- **Медленные запросы** при длинных chain: несколько JOIN через большие таблицы.
  Митигация: `causal_root_id` индексирован во всех таблицах. Пагинация обязательна.
- **Неполные chains** за retention window — это не баг, это задокументированное поведение.
  Caller обязан проверять `chain_complete`.
- Causal query layer — internal diagnostic, не public API. Доступ только для:
  оператора (DLQ repair), system audit jobs, postmortem tooling.

---

## Implementation Notes

### 1. Три класса Causal Query Surface

Система обязана отвечать на эти вопросы через query layer:

```text
Класс A: Chain by root
  Вопрос: "что произошло в системе из-за события X?"
  Input:  causal_root_id
  Output: все OutboxRecord + ProcessedEvent + DLQ records с этим causal_root_id

Класс B: Chain by correlation
  Вопрос: "что произошло в рамках этого HTTP-запроса / бизнес-операции?"
  Input:  correlation_id
  Output: все события с этим correlation_id, упорядоченные по времени

Класс C: Aggregate history
  Вопрос: "в каком порядке обрабатывались события этого агрегата?"
  Input:  (tenant_id, aggregate_type, aggregate_id)
  Output: OutboxRecord ordered by aggregate_seq, с ProcessedEvent статусами
```

Класс C — только в пределах retention window (OutboxRecord должен существовать).
Классы A и B — работают частично даже после архивации (через ProcessedEvent и domain tables).

### 2. CausalChain — возвращаемая структура

```python
@dataclass(slots=True, frozen=True)
class CausalNode:
    outbox_record_id: uuid.UUID
    causal_root_id: uuid.UUID
    correlation_id: uuid.UUID
    aggregate_id: str
    aggregate_type: str
    aggregate_seq: int
    tenant_id: uuid.UUID
    occurred_at: datetime
    event_type: str
    # Статус обработки (из ProcessedEvent, если существует)
    processing_status: Literal["pending", "claimed", "acked", "dlq", "archived"] | None
    # NULL если OutboxRecord уже архивирован
    outbox_archived: bool

@dataclass(slots=True, frozen=True)
class CausalChain:
    root_id: uuid.UUID                # causal_root_id запроса
    nodes: tuple[CausalNode, ...]     # все найденные узлы, ordered by occurred_at
    chain_complete: bool              # False если часть цепи архивирована или за retention window
    missing_layers: tuple[str, ...]   # какие таблицы вернули пустой результат
    queried_at: datetime
    queried_by: str                   # operator_id — обязателен, для Finance audit
```

### 3. CausalQueryService

```python
class CausalQueryService:

    @staticmethod
    def get_chain_by_root(
        causal_root_id: uuid.UUID,
        tenant_id: uuid.UUID,
        queried_by: str,
    ) -> CausalChain:
        """
        Класс A: полная цепь по causal_root_id.
        Читает из OutboxRecord, ProcessedEvent, DeadLetterRecord.
        chain_complete=False если OutboxRecord архивирован или отсутствует.
        Всегда логирует access (rule 5.8 для Finance tenant).
        """

    @staticmethod
    def get_chain_by_correlation(
        correlation_id: uuid.UUID,
        tenant_id: uuid.UUID,
        queried_by: str,
    ) -> CausalChain:
        """
        Класс B: все события в рамках correlation_id.
        Не зависит от causal_root_id — работает через ProcessedEvent.correlation_id.
        """

    @staticmethod
    def get_aggregate_history(
        tenant_id: uuid.UUID,
        aggregate_type: str,
        aggregate_id: str,
        queried_by: str,
        cursor: int | None = None,
        limit: int = 50,
    ) -> CausalChain:
        """
        Класс C: история агрегата с курсорной пагинацией по aggregate_seq.
        Только в пределах retention window.
        """
```

### 4. Индексы — обязательны до реализации query layer

```sql
-- OutboxRecord
CREATE INDEX CONCURRENTLY IF NOT EXISTS outbox_causal_root_idx
    ON common_transactional_outbox (tenant_id, causal_root_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS outbox_correlation_idx
    ON common_transactional_outbox (tenant_id, correlation_id);

-- ProcessedEvent
CREATE INDEX CONCURRENTLY IF NOT EXISTS pe_causal_root_idx
    ON common_processed_events (causal_root_id);

-- DeadLetterRecord
CREATE INDEX CONCURRENTLY IF NOT EXISTS dlq_causal_root_idx
    ON common_dead_letter_records (causal_root_id)
    WHERE causal_root_id IS NOT NULL;
```

Без этих индексов `get_chain_by_root()` — sequential scan по всем таблицам.

### 5. Audit logging для Finance

```python
# rule 5.8: каждый read PII / Finance causal data логируется
logger.info(
    "causal_query_executed",
    query_class="chain_by_root",
    causal_root_id=str(causal_root_id),
    tenant_id=str(tenant_id),
    queried_by=queried_by,
    chain_complete=result.chain_complete,
    node_count=len(result.nodes),
)
```

### 6. Что causal query layer НЕ делает

- Не пишет в какие-либо таблицы.
- Не инициирует repair или replay.
- Не агрегирует метрики — это Celery beat monitoring task (ADR-0014, section 6).
- Не является публичным API: нет Django Ninja endpoint. Только internal Python service.

---

## Causal Query Layer — полная карта вопросов и ответов

| Вопрос | Query Class | Primary axis | chain_complete при архивации |
| --- | --- | --- | --- |
| Что случилось из-за закрытия контракта X? | A | causal_root_id | False |
| Что произошло в рамках HTTP-запроса R? | B | correlation_id | False (если есть OutboxRecord) |
| Как обрабатывались события агрегата A? | C | aggregate_seq | False — только в retention window |
| Почему Finance transaction T появилась? | A | causal_root_id из external_ref | False после retention |
| Был ли repair для события X? | A | causal_root_id | False после retention |

---

## Review Date

Пересмотреть при:
- P99 latency `get_chain_by_root()` > 500ms: индексы недостаточны или chain слишком длинная.
- Требовании публичного Causal API (внешний аудит, юридический запрос): нужен отдельный ADR
  с rate limiting, RBAC и audit trail по каждому внешнему запросу.
- Переходе к Event Sourcing: `CausalChain` станет projection над event stream, не над
  transport tables.

---

## Связанные ADR

- [ADR-0015](0015-causal-contract-layer.md) — CausalUnit и инварианты; query layer читает только поля из CausalUnit
- [ADR-0013](0013-outbox-retention-and-dlq-repair.md) — retention window определяет `chain_complete`; query layer не может восстановить за его пределами
- [ADR-0014](0014-cross-domain-consistency-divergence-model.md) — Класс A запросы используются для verifying causal pair closure
- [ADR-0009](0009-pii-vault-query-masking.md) — Finance causal queries подпадают под rule 5.8 PII access logging
