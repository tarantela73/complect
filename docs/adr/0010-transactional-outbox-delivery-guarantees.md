# 0010 — Гарантии доставки Transactional Outbox

## 1. Статус

| Поле       | Значение                            |
| ---------- | ----------------------------------- |
| Статус     | APPROVED                            |
| Дата       | 2026-06-03                          |
| Владелец   | Platform / Common Infrastructure    |
| Заменяет   | —                                   |
| Заменён на | —                                   |

---

## 2. Контекст

Система требует надёжной асинхронной доставки доменных событий между bounded contexts
(rule 4.3, ADR-0005). Прямая публикация в Redis Streams из бизнес-транзакции нарушает
атомарность: Redis-запись может упасть, а DB-транзакция уже commited, или наоборот.

Необходима формальная спецификация гарантий на каждом слое pipeline, чтобы:

- модели `OutboxRecord` и `ProcessedEvent` реализовывали именно задокументированный контракт;
- consumer-разработчики понимали, что им гарантирует транспорт, а что они должны обеспечить сами;
- изменения гарантий требовали явного обновления этого ADR.

---

## 3. Решение

### Архитектурная модель

Шесть последовательных слоёв с компенсацией на каждом:

```
[1] DB Outbox         ─ strong durability (DB transaction)
      ↓
[2] Wake-up layer     ─ coalesced trigger, crash-safe via inflight TTL
      ↓
[3] Dispatcher        ─ at-least-once attempt + retry/backoff + SKIP LOCKED
      ↓
[4] Stream transport  ─ at-least-once (Redis Streams)
      ↓
[5] Consumer dedup    ─ exactly-once effect (DB unique constraint)
      ↓
[6] ACK ownership     ─ ownership-bound completion
```

Итоговая семантика: **stacked at-least-once with idempotency collapse**.

### Правила

- `OutboxRecord` пишется внутри бизнес-транзакции — никогда после commit.
- Redis publish происходит строго через `transaction.on_commit()`.
- Dispatcher использует `SELECT FOR UPDATE SKIP LOCKED` до `[:batch]` среза (lock-before-slice).
- `ProcessedEvent.claim()` — единственный gate для consumer-side dedup; INSERT + IntegrityError.
- `ProcessedEvent.ack()` требует `claimed_by_instance_id` match, если `instance_id` передан.
- Изменение любой гарантии в матрице ниже требует обновления этого ADR.

### Запреты

- Прямой `xadd` внутри бизнес-транзакции без Outbox.
- `select_for_update()` после начального filter-slice (lock-after-select).
- `ack()` без предшествующего `claim()` от того же инстанса (при использовании `instance_id`).

### Гарантийная матрица

#### Легенда

| Символ | Значение |
|--------|----------|
| ✓ strong | гарантия обеспечена конструкцией |
| ~ eventual | гарантируется только при нормальной работе / ретраях |
| ✗ none | нет гарантии |
| ⊕ deduplicated | дубли возможны, но подавляются |

#### Слой 1 — Outbox Writer (`TransactionalOutboxService`)

| Сценарий | Гарантия | Механизм |
|----------|----------|----------|
| Event записан в БД | ✓ strong | Django transaction |
| Event потерян при crash до commit | ✓ strong | DB transaction rollback |
| Event потерян после commit | ✓ strong | запись уже в outbox |
| Двойная запись события | ✓ strong | отдельные rows |
| Дубли wake-up dispatch | ⊕ deduplicated | SETNX cache + TTL |

> Outbox = source of truth, не зависит от Celery/Redis.

#### Слой 2 — Wake-up Layer (`on_commit` + coalescing + inflight)

| Сценарий | Гарантия | Механизм |
|----------|----------|----------|
| Burst writes → N commits | ⊕ → ✓ coalesced | SETNX `outbox:dispatch:wakeup` (TTL=2s) |
| Celery task crash без cleanup | ✓ self-healing | `outbox:dispatch:inflight` TTL=30s |
| Redis lock contention | ✓ safe | cache.add атомарен |
| Duplicate dispatcher start | ⊕ deduplicated | inflight guard |

> wake-up — trigger layer, не execution layer.

#### Слой 3 — Dispatcher (`run_outbox_polling_dispatcher_task`)

| Сценарий | Гарантия | Механизм |
|----------|----------|----------|
| Parallel workers pick same rows | ✓ fixed | `SELECT FOR UPDATE SKIP LOCKED` |
| Partial batch failure | ✓ isolated | per-record try/except |
| Redis failure | ✓ retryable | retry_count + exponential backoff |
| Permanent failure | ✓ terminal state | `failed_permanently_at` |
| Reordering | ✗ none | нет `sequence_number` per tenant; backoff нарушает порядок |

> Dispatcher = at-least-once attempt с идемпотентными переходами состояния (`is_processed`).
>
> **Скрытая уязвимость:** `xadd` успешен → DB `save()` упал → dispatcher retries → второй
> `xadd`. Дубль подавляется слоем 5, который поэтому **обязателен**.

#### Слой 4 — Stream Transport (Redis Streams)

| Сценарий | Гарантия | Механизм |
|----------|----------|----------|
| Message duplication | ✓ expected | at-least-once design |
| Message loss при Redis crash | ✗ none | зависит от AOF/RDB конфигурации |
| Partial publish failure | ✓ retry | dispatcher retry loop |
| Multi-tenant isolation | ✓ logical | отдельный stream per tenant |

> Redis Stream = transport, not guarantee layer.

#### Слой 5 — Consumer Identity (`ProcessedEvent`)

| Сценарий | Гарантия | Механизм |
|----------|----------|----------|
| Duplicate delivery from Redis | ✓ deduplicated | `unique(outbox_record_id, consumer)` |
| Race между consumers | ✓ safe | DB constraint |
| Retry того же события | ⊕ idempotent | INSERT + IntegrityError |
| Crash mid-processing | ✗ none | нет ack → запись claimed, не завершена |
| Replay detection | ✓ possible | `acked_at IS NULL` |

> Consumer layer = exactly-once effect simulation.

#### Слой 6 — ACK / Ownership

| Сценарий | Гарантия | Механизм |
|----------|----------|----------|
| Wrong instance ACK | ✓ blocked | `claimed_by_instance_id` mismatch |
| Double ACK | ⊕ idempotent | `acked_at IS NULL` filter |
| Lost ACK call | ~ retryable | consumer retry logic |
| Zombie consumer recovery | ✓ auto-recoverable | `lease_expires_at` TTL + `reclaim()` (ADR-0011) |

#### End-to-end delivery semantics

| Этап | Семантика |
|------|-----------|
| Outbox write | exactly-once (DB transaction) |
| Wake-up | at-least-once trigger |
| Dispatch | at-least-once attempt, idempotent state |
| Stream | at-least-once transport |
| Consumer processing | effectively-once (via dedup) |
| ACK | ownership-bound completion |

#### Где гарантий нет (честно)

| Слой | Уязвимость |
|------|------------|
| Redis Stream | дублирование сообщений |
| Dispatcher | переотправка при crash между `xadd` и DB update |
| Consumer processing | выполнено, но ack не дошёл |
| Ordering | ✗ none — нет `sequence_number` per tenant |
| Zombie consumer | legacy claims (`lease_expires_at=NULL`) — не auto-recoverable; opt-in через `lease_ttl=` |

---

## 4. Рассмотренные альтернативы

| Альтернатива | Причина отказа |
| ------------ | -------------- |
| Прямой publish в Redis из транзакции | Нарушает атомарность: Redis не участвует в DB-транзакции |
| Kafka / RabbitMQ | Внешняя зависимость, операционная сложность; PostgreSQL достаточен при текущих объёмах |
| Exactly-once через 2PC (XA) | PostgreSQL + Redis не поддерживают XA; overhead неоправдан |
| Ordering через per-tenant stream partition (hard) | Избыточно до появления явной бизнес-потребности; covered by ADR-0011 soft-ordering |

---

## 5. Последствия

### Плюсы

- Гарантии доставки явно задокументированы и привязаны к моделям.
- Каждый слой компенсирует уязвимости следующего — система устойчива к частичным отказам.
- Consumer API backward-compatible: `instance_id` опционален.
- Beat schedule как fallback recovery не требует внешних компонентов.

### Минусы

- Ordering не гарантирован — consumers не должны полагаться на порядок событий.
- Zombie consumers требуют ручного или периодического scan до ADR-0011.
- `xadd` → DB crash window дублирует сообщения: consumer dedup обязателен, не опционален.

---

## 6. Связанные ADR и разделы

- [ADR-0002](0002-zero-downtime-migration-governance.md) — Expand & Contract для миграций `ProcessedEvent`
- [ADR-0005](0005-bounded-context-ownership.md), Law 3 — Cross-domain async обязательно проходит через Transactional Outbox, не через прямой import
- ADR-0011 (pending) — Outbox Lease Dispatcher: `lease_until`, авто-release, soft ordering guarantees
