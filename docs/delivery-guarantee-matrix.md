# Гарантийная матрица доставки (Outbox → Stream → Consumer)

## Легенда

| Символ | Значение |
|--------|----------|
| ✓ strong | гарантия обеспечена конструкцией |
| ~ eventual | гарантируется только при нормальной работе / ретраях |
| ✗ none | нет гарантии |
| ⊕ deduplicated | дубли возможны, но подавляются |

---

## 1. OUTBOX WRITER (`TransactionalOutboxService`)

| Сценарий | Гарантия | Механизм |
|----------|----------|----------|
| Event записан в БД | ✓ strong | Django transaction |
| Event потерян при crash до commit | ✓ strong | DB transaction rollback |
| Event потерян после commit | ✓ strong | запись уже в outbox |
| Двойная запись события | ✓ strong | отдельные rows |
| Дубли wake-up dispatch | ⊕ deduplicated | SETNX cache + TTL |

> **Ключевое:** Outbox = source of truth, не зависит от Celery/Redis.

---

## 2. WAKE-UP LAYER (`on_commit` + coalescing + inflight)

| Сценарий | Гарантия | Механизм |
|----------|----------|----------|
| Burst writes → N commits | ⊕ → ✓ coalesced | SETNX wake-up key |
| Celery task crash | ✓ self-healing | inflight TTL fallback |
| Redis lock contention | ✓ safe | cache + TTL |
| Duplicate dispatcher start | ⊕ deduplicated | inflight guard |

> **Ключевое:** wake-up — это trigger layer, не execution layer.

---

## 3. DISPATCHER (Outbox polling worker)

| Сценарий | Гарантия | Механизм |
|----------|----------|----------|
| Parallel workers pick same rows | ✗ → ✓ fixed | `SELECT FOR UPDATE SKIP LOCKED` |
| Partial batch failure | ✓ isolated | per-record try/except |
| Redis failure | ✓ retryable | retry_count + backoff |
| Permanent failure | ✓ terminal state | `failed_permanently_at` |
| Reordering | ✗ none | нет `sequence_number` per tenant; backoff смещает порядок |

> **Ключевое:** dispatcher = **at-least-once attempt** engine с идемпотентными переходами
> состояния (`is_processed`). Попытка — at-least-once; состояние записи — идемпотентно.
>
> **Скрытая дыра:** `xadd` успешен → DB `save()` упал (network partition) → dispatcher
> retries → второй `xadd` в стрим. Дубль подавляется Consumer Identity Layer (секция 5),
> которая поэтому **обязательна**, не опциональна.

---

## 4. STREAM DELIVERY (Redis Streams)

| Сценарий | Гарантия | Механизм |
|----------|----------|----------|
| Message duplication in stream | ✓ expected | at-least-once design |
| Message loss in Redis crash | ✗ none | зависит от AOF/RDB конфигурации Redis |
| Partial publish failure | ✓ retry | dispatcher retry loop |
| Multi-tenant isolation | ✓ logical | stream per tenant |

> **Ключевое:** Redis Stream = transport, not guarantee layer.

---

## 5. CONSUMER IDENTITY LAYER (`ProcessedEvent`)

| Сценарий | Гарантия | Механизм |
|----------|----------|----------|
| Duplicate delivery from Redis | ✓ deduplicated | `unique(outbox_id, consumer)` |
| Race between consumers | ✓ safe | DB constraint |
| Retry of same event | ⊕ idempotent | INSERT + IntegrityError |
| Crash mid-processing | ✗ none | нет ack → запись claimed, но не завершена |
| Replay detection | ✓ possible | `acked_at IS NULL` |

> **Ключевое:** Consumer layer = exactly-once effect simulation.

---

## 6. ACK / OWNERSHIP LAYER

| Сценарий | Гарантия | Механизм |
|----------|----------|----------|
| Wrong instance ACK | ✓ blocked | `instance_id` mismatch |
| Double ACK | ⊕ idempotent | `acked_at IS NULL` filter |
| Lost ACK call | ~ retryable | consumer retry logic |
| Zombie consumer recovery | ✓ detectable, ✗ auto-recoverable | `acked_at IS NULL` + TTL inference; автоматический re-claim отсутствует → ADR-004 |

> **Ключевое:** ACK = completion proof, not execution trigger.
>
> **Открытый gap:** claim() успешен → consumer crash до ack(). Запись занята
> (`acked_at IS NULL`), уникальный constraint блокирует re-claim другим инстансом.
> Автоматического release нет — нужен lease с `lease_until` timestamp (ADR-004:
> Outbox Lease Dispatcher).

---

## END-TO-END MATRIX

### Delivery semantics по всей системе

| Этап | Семантика |
|------|-----------|
| Outbox write | exactly-once (DB transaction) |
| Wake-up | at-least-once trigger |
| Dispatch | at-least-once attempt, idempotent state |
| Stream | at-least-once transport |
| Consumer processing | effectively-once (via dedup) |
| ACK | ownership-bound completion |

### Итоговая модель

```
[1] DB Outbox
     ↓ (strong durability)

[2] Wake-up layer
     ↓ (coalesced trigger, crash-safe via inflight TTL)

[3] Dispatcher
     ↓ (at-least-once attempt + retry/backoff + SKIP LOCKED)

[4] Stream transport
     ↓ (at-least-once)

[5] Consumer idempotency
     ↓ (exactly-once effect via DB constraint)

[6] ACK ownership
     ↓ (completion correctness, instance-bound)
```

### Где всё ещё НЕ exactly-once (честно)

| Слой | Уязвимость |
|------|------------|
| Redis Stream | может дублировать сообщения |
| Dispatcher | переотправит при crash между `xadd` и DB update |
| Consumer processing | выполнено, но ack не дошёл |
| Ordering | ✗ none — нет `sequence_number` per tenant, backoff нарушает порядок |
| Zombie consumer | acked_at=NULL без авто-release до ADR-004 |

> **Почему это нормально:** каждый уровень компенсирует уязвимость следующего.
> Это классическая модель: **"stacked at-least-once with idempotency collapse"**.

---

## Связанные ADR

- [ADR-0002](adr/0002-zero-downtime-migration-governance.md) — Expand & Contract миграции
- ADR-004 (pending) — Outbox Lease Dispatcher: `lease_until` + авто-release, устранение
  polling hot loop, автоматический recovery зомби-consumers
