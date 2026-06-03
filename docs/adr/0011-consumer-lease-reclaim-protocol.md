# ADR-0011: Consumer Lease & Reclaim Protocol

**Date:** 2026-06-03
**Status:** Accepted
**Author(s):** Platform / Common Infrastructure
**Deciders:** Platform Team

---

## Context

После реализации Consumer Idempotency Layer (ADR-0010) система гарантирует delivery
idempotency: одно событие не будет обработано дважды одновременно. Однако существует
критический gap: если consumer выполнил бизнес-операцию и упал до вызова `ack()`,
запись `ProcessedEvent` остаётся в состоянии `acked_at IS NULL` — claim занят, но
никем не завершается. Уникальный constraint на `(outbox_record_id, consumer)` блокирует
любую попытку другого инстанса забрать это событие.

Для доменов с финансовыми side-effect'ами (TransactionPostingService, ContractClosedEvent)
это означает: бухгалтерская проводка может быть создана, но событие никогда не будет
помечено как завершённое. При ручном replay — дублирование проводки.

**Key constraints:**

- Lease release должен быть атомарным — два воркера не могут одновременно reclaim одно событие.
- Backward compatibility: существующие consumers без `lease_ttl` не должны сломаться.
- Нет внешних зависимостей за пределами PostgreSQL — Redis-based lease вносит ещё один SPOF.
- Бизнес-idempotency (отсутствие дублей side-effect) остаётся ответственностью domain layer, а не transport layer.

---

## Options Considered

### Option 1: TTL-based Lease с `lease_expires_at`

**Description:** Добавить поле `lease_expires_at` к `ProcessedEvent`. `claim()` устанавливает
дедлайн владения. Новый метод `reclaim()` атомарно перехватывает истёкший claim через
`UPDATE WHERE acked_at IS NULL AND lease_expires_at < now()`. Опциональный `heartbeat()`
продлевает lease для long-running consumers.

**Pros:**

- Атомарный reclaim через одиночный UPDATE — нет race condition.
- Полностью в PostgreSQL: нет зависимости от Redis для lease semantics.
- Opt-in: `lease_ttl=None` сохраняет старое поведение.
- Heartbeat позволяет consumer'у явно сигнализировать о живости.

**Cons:**

- `lease_expires_at` не учитывает случай "consumer жив, но завис в бесконечном цикле".
- Выбор TTL — компромисс: слишком короткий → ложные reclaim; слишком длинный → долгое восстановление.

### Option 2: Heartbeat-only (без hard TTL)

**Description:** Consumer периодически обновляет `heartbeat_at`. Отдельный background job
сканирует записи где `heartbeat_at < now() - TIMEOUT` и помечает как abandoned.

**Pros:**

- Явная сигнализация живости.

**Cons:**

- Требует background scanner — дополнительная движущаяся часть.
- "Abandoned" ≠ "releasable": нужна отдельная логика reclaim.
- Без hard TTL — scanner может не запуститься → zombie живёт вечно.

**Why ruled out:** Два механизма (heartbeat + scanner) вместо одного (TTL + reclaim). TTL достаточен для recovery, heartbeat опционально дополняет его.

### Option 3: Redis-based Lease (SETNX + EXPIRE)

**Description:** Lease хранится как Redis ключ `consumer:lease:{event_id}:{consumer}`.
TTL — в Redis. ProcessedEvent не меняется.

**Pros:**

- Не меняет схему БД.
- Мгновенная проверка через Redis.

**Cons:**

- Redis SPOF: если Redis недоступен — нельзя ни взять, ни освободить lease.
- Lease state и ProcessedEvent state в разных хранилищах → инварианты сложнее.
- При Redis flush все lease теряются → все claimed события становятся zombie.

**Why ruled out:** Добавляет SPOF без выигрыша в надёжности. PostgreSQL достаточен.

### Option 4: Saga / Process Manager (полная компенсация)

**Description:** Каждая бизнес-операция регистрирует compensating action. При failure — компенсация.

**Pros:**

- Закрывает business effect idempotency (двойная проводка невозможна).

**Cons:**

- Масштаб изменений несоразмерен задаче.
- Компенсирующие транзакции сложны для финансовых операций (уже реализован reverse через `TransactionPostingService`).

**Why ruled out:** Правильный паттерн, неправильный уровень. Business idempotency решается в domain layer (уникальный `external_ref` + unique constraint в Finance), не в transport layer.

---

## Decision

**Реализуем TTL-based Lease с `lease_expires_at` (Option 1) с семантикой safe-by-default.**

К `ProcessedEvent` добавляется поле `lease_expires_at`. Lease **включён по умолчанию**:
`claim()` без `lease_ttl` использует `settings.CONSUMER_LEASE_TTL_SECONDS`. Бесконечный
lease требует явного `claim(lease_ttl=None)` — это unsafe opt-out, не дефолт.
`reclaim()` атомарно перехватывает истёкший claim через `UPDATE ... WHERE lease_expires_at < now()`.
`heartbeat()` продлевает lease для long-running обработчиков.

**API-контракт:**

```python
claim()               # lease = settings.CONSUMER_LEASE_TTL_SECONDS  (recoverable by default)
claim(lease_ttl=None) # lease = NULL, infinite                        (explicit unsafe opt-out)
claim(lease_ttl=N)    # lease = N seconds                             (custom TTL)
```

Это модель **safe-by-default, unsafe-by-opt-in**: каждый новый consumer автоматически
recoverable без необходимости явно передавать `lease_ttl`.

---

## Consequences

### Positive Consequences

- Все новые consumers recoverable-by-default — нет дрейфа к смешанной модели.
- Zombie consumers автоматически recoverable: другой инстанс может reclaim после TTL.
- Нет ручного вмешательства для recovery при crash.
- Reclaim атомарен через DB UPDATE — нет distributed lock.
- Контракт защищён тестами: `test_claim_without_lease_argument_creates_default_lease`
  и `test_claim_with_explicit_none_creates_infinite_lease`.

### Negative Consequences / Accepted Tradeoffs

- **Business effect idempotency не закрыта этим ADR.** Finance domain обеспечивает
  идемпотентность через `UNIQUE(tenant_id, external_ref) WHERE external_ref != ''`.
  Lease & Reclaim — transport-layer решение, не business-layer.
- TTL выбирается статически: при длительной обработке consumer ДОЛЖЕН вызывать
  `heartbeat()` до истечения lease, иначе reclaim произойдёт раньше завершения.
- Записи с `lease_expires_at=NULL` (явный opt-out) не recoverable через `reclaim()` —
  требуют ручного анализа или backfill job при переходе к полностью управляемой модели.

### Risks

- Слишком короткий `CONSUMER_LEASE_TTL_SECONDS` (< P99 processing time) → ложные reclaim
  при нормальной работе → дублирование обработки.
- Consumer не вызывает `heartbeat()` при долгой операции → reclaim в середине транзакции.
  Митигация: `heartbeat()` вызывается каждые `TTL/3` секунд.
- Решение пересматривается при масштабировании consumer fleet > 50 инстансов.

---

## Implementation Notes

### Схема изменений

```
ProcessedEvent
  + lease_expires_at: DateTimeField(null=True, blank=True)
```

### Протокол для consumer (новый стиль)

```python
instance_id = uuid.uuid4()
if ConsumerIdempotencyService.claim(event_id, consumer, tenant_id,
                                    instance_id=instance_id,
                                    lease_ttl=CONSUMER_LEASE_TTL_SECONDS):
    _process_and_ack(event_id, consumer, instance_id)
elif ConsumerIdempotencyService.reclaim(event_id, consumer, tenant_id,
                                        instance_id=instance_id,
                                        lease_ttl=CONSUMER_LEASE_TTL_SECONDS):
    _process_and_ack(event_id, consumer, instance_id)
# else: valid claim held by another instance — skip
```

### Протокол для long-running consumer

```python
# Каждые TTL/3 секунд во время обработки:
ConsumerIdempotencyService.heartbeat(event_id, consumer, instance_id,
                                     lease_ttl=CONSUMER_LEASE_TTL_SECONDS)
```

### Атомарность reclaim

`reclaim()` реализуется как `UPDATE ... WHERE acked_at IS NULL AND lease_expires_at IS NOT NULL AND lease_expires_at < now()`. Два конкурирующих воркера: первый UPDATE затрагивает 1 строку и устанавливает новый `lease_expires_at`; второй UPDATE условие `lease_expires_at < now()` уже не выполняется → 0 строк → `reclaim()` возвращает False.

### Рекомендуемые значения TTL

| Consumer type | `lease_ttl` | `heartbeat` interval |
|---------------|-------------|----------------------|
| Fast (< 5s) | 60s | — |
| Medium (< 60s) | 300s | 100s |
| Long-running | 300s | 100s (обязателен) |

### Настройка

```python
# config/settings/base.py
CONSUMER_LEASE_TTL_SECONDS = 300
```

### Migration

Expand & Contract (ADR-0002): Release N — только `AddField(lease_expires_at, null=True)`.
`NOT NULL` + backfill — Release N+2 (если потребуется).

---

## Review Date

Пересмотреть при:
- P99 processing time любого consumer превышает `CONSUMER_LEASE_TTL_SECONDS / 2`
- Количество reclaim-событий в логах превышает 1% от total claims (ложные reclaim)
- Реализации ADR-0012 (Outbox Lease Dispatcher) — возможна унификация lease механизмов

---

## Связанные ADR

- [ADR-0010](0010-transactional-outbox-delivery-guarantees.md) — гарантийная матрица; zombie consumer row обновляется после реализации
- [ADR-0002](0002-zero-downtime-migration-governance.md) — Expand & Contract для `lease_expires_at`
- ADR-0012 (pending) — Outbox Lease Dispatcher, soft ordering guarantees
