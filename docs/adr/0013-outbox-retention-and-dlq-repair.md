# ADR-0013: Outbox Retention Policy & Dead-Letter Repair Semantics

**Date:** 2026-06-03
**Status:** Accepted
**Author(s):** Platform / Common Infrastructure
**Deciders:** Platform Team

---

## Context

После ADR-0010–0012 система образует три слоя истины с разными гарантиями хранения:

| Слой | Хранилище | Роль | Текущий lifecycle |
| --- | --- | --- | --- |
| Transport truth | `OutboxRecord` | delivery buffer | удаляется / архивируется? — **не определено** |
| Consumption truth | `ProcessedEvent` / `AggregateSequenceFence` | delivery ledger | не определено |
| Business truth | domain tables | canonical state | permanent |

Ни одна из трёх сущностей не имеет формального retention contract. Это означает: система
функционирует корректно в normal flow, но **recovery boundary не определена**. Если спросить
«можно ли восстановить состояние consumer'а за прошлый месяц?», честного ответа нет.

Помимо этого, при анализе `AdminDlqManagementService.execute_task_replay()` обнаружены два
структурных gap:

**Gap A** — `xdel` выполняется сразу после `send_task`. Если consumer после replay повторно
упадёт, запись уже удалена из Redis Stream. Replay невозможен без ручного восстановления.

**Gap B** — если consumer успел выставить `acked_at` до crash'а, повторный dispatch из DLQ
проходит через `claim()` → False, `reclaim()` → False и молча скипается. Business action
может быть не выполнен, но система считает событие обработанным.

**Key constraints:**

- Система является ERP, а не Event-Sourced. `OutboxRecord` — delivery buffer, не event store.
  Полная история событий для произвольного rebuild — не требование бизнеса.
- Canonical system of record — domain tables. Это не меняется.
- `failed_permanently_at IS NOT NULL` records и DLQ records — разные классы данных с разным
  lifecycle: первые фиксируют publish-failure, вторые — consumer-failure.
- Любой repair action в Finance domain требует human sign-off и audit trail.

---

## Options Considered

### Option 1: OutboxRecord как event log (Path A)

**Description:** `OutboxRecord` превращается в immutable event store. `is_processed=True` не
означает «готов к архивированию». Записи хранятся indefinitely или с длинным retention (годы).
Появляется полноценный replay from seq=1.

**Pros:**
- Детерминированный rebuild consumer state за любой период.
- Полная история событий без отдельного event store.

**Cons:**
- `OutboxRecord` сейчас не содержит всего контекста для Event Sourcing (нет actor, причины,
  pre-state). Это partial event log, а не canonical event log.
- Storage растёт O(events) навсегда: при 1M events/день — ~365M records/год.
- Retention management становится отдельной сложной системой (tiered storage, archival queries).
- `maxlen=10000` на Redis Stream уже обрезает историю — это фундаментальное противоречие
  с event log semantics.

**Why ruled out:** Требует изменения контракта OutboxRecord (add actor, pre/post state, reason)
и решения storage scaling. Это Event Sourcing migration, не retention policy.

---

### Option 2: Ничего не менять (implicit retention)

**Description:** Продолжать без формального retention contract. OutboxRecord cleanup — по
усмотрению ops. DLQ repair — через `execute_task_replay` как есть.

**Pros:**
- Нет работы сейчас.

**Cons:**
- Gap A и Gap B остаются: DLQ replay ненадёжен при повторных сбоях.
- `failed_permanently_at` records накапливаются без lifecycle.
- Recovery boundary не определена → невозможно отвечать на вопросы аудита и postmortem.

**Why ruled out:** Это не выбор, а отложенная проблема. Gaps A и B — production risks.

---

### Option 3 (выбрано): Bounded Delivery Buffer + Quarantine Contract + Repair Protocol

**Description:** Система остаётся на Path B (delivery buffer, не event store). Вводится:

1. **Retention contract** для `OutboxRecord`: обычные processed records — archive window.
   `failed_permanently_at IS NOT NULL` records — quarantine class, не удаляются без явного action.

2. **Quarantine contract** для DLQ: записи в `infrastructure:dlq:stream` хранятся до
   explicit human sign-off. `xdel` происходит только после подтверждённого успешного repair.

3. **Repair protocol**: явный двухшаговый процесс для stuck events (Gap B): сначала
   `ProcessedEvent` reset через `RepairService`, затем replay.

**Pros:**
- Закрывает оба production gap без изменения архитектурного контракта.
- Формализует recovery boundary: «replay возможен в пределах retention window».
- DLQ record не теряется при повторном сбое consumer'а после replay.
- Repair требует явного действия — нет случайных side-effect.

**Cons:**
- Repair protocol добавляет операционный overhead для Finance events (human sign-off).
- Retention job — новая движущаяся часть.

---

## Decision

**Реализуем Bounded Delivery Buffer + Quarantine Contract + Repair Protocol (Option 3).**

`OutboxRecord` остаётся delivery buffer с явными retention классами. Domain tables — canonical
system of record. Replay гарантирован только в пределах retention window. DLQ records
переходят в режим quarantine: `xdel` происходит только после явного `mark_repaired()`,
не после `send_task`. Для stuck events (Gap B) вводится `RepairService.reset_for_repair()`
с обязательным audit log.

**Recovery boundary (явно):** восстановление состояния consumer возможно только если соответствующий
`OutboxRecord` не архивирован. За пределами retention window — нет гарантий, только domain state.

---

## Consequences

### Positive Consequences

- Gap A закрыт: запись в DLQ не исчезает при повторном сбое consumer'а.
- Gap B закрыт: stuck events восстанавливаемы через явный repair protocol.
- Recovery boundary задокументирована и отвечаема на аудите.
- `failed_permanently_at` records сохраняются как криминалистические улики до explicit action.

### Negative Consequences / Accepted Tradeoffs

- **Replay не гарантирован за пределами retention window.** Это осознанный трейдофф:
  система не является event store. Для долгосрочного аудита необходим отдельный audit log
  (domain responsibility, не transport responsibility).
- **Repair требует human sign-off** для Finance domain. Это operational overhead, но необходим
  для correctness: автоматический repair Finance events без проверки = risk of duplicate posting.
- Retention job — дополнительная Celery task. При сбое job: `OutboxRecord` накапливаются,
  но не теряются — это acceptable degradation.

### Risks

- `ProcessedEvent` reset в `RepairService` — опасная операция при неверном использовании.
  Если reset выполнен для уже корректно обработанного события, consumer получит двойной trigger.
  Митигация: `reset_for_repair()` проверяет `failed_permanently_at` или DLQ-origin before reset.
- Retention job удаляет archived records — нератимо. Митигация: архивация в отдельную таблицу
  (`common_outbox_archive`) вместо DELETE, с последующей ручной очисткой.
- Пересматривается при: требовании audit-grade event history → тогда нужен отдельный event log ADR.

---

## Implementation Notes

### 1. OutboxRecord — retention классы

Три lifecycle class, определяются по состоянию полей:

```python
# Класс 1: Normal — обработан, готов к архивированию
is_processed=True AND failed_permanently_at IS NULL
→ archive after OUTBOX_ARCHIVE_WINDOW_DAYS (default: 30)

# Класс 2: Quarantine — publish failure, требует repair или sign-off
failed_permanently_at IS NOT NULL
→ НЕ архивируется автоматически. Требует explicit action.

# Класс 3: Pending — ещё не обработан
is_processed=False AND failed_permanently_at IS NULL
→ active, не трогать
```

Архивация через Celery retention job (`retention` queue):

```python
@app.task(base=BaseEnterpriseTask, queue="retention")
def archive_processed_outbox_records_task() -> None:
    cutoff = tz.now() - timedelta(days=settings.OUTBOX_ARCHIVE_WINDOW_DAYS)
    qs = OutboxRecord.objects.filter(
        is_processed=True,
        failed_permanently_at__isnull=True,
        processed_at__lt=cutoff,
    )
    # INSERT INTO common_outbox_archive SELECT ... FROM common_transactional_outbox WHERE ...
    # затем DELETE WHERE id IN (архивированные id)
    # Batch size: 500, SKIP LOCKED
```

Таблица архива (`common_outbox_archive`) — идентичная схема, без индексов кроме PK.
Ротация архива — ручная, вне scope этого ADR.

### 2. DLQ repair — fix Gap A

**Текущий баг:** `xdel` до подтверждения repair результата.

**Fix:** разделить `replay` и `mark_repaired` на два отдельных действия:

```python
class AdminDlqManagementService:

    @classmethod
    def execute_task_replay(cls, sequence_id: str) -> str:
        """
        Dispatch DLQ task for re-processing. Does NOT delete the record.
        Returns a repair_token that must be passed to mark_repaired() on success.
        """
        guard_key = cls.REPLAY_GUARD_PREFIX + sequence_id
        if settings.REDIS_CLIENT.set(guard_key, "1", nx=True, ex=86400) is False:  # 24h guard
            raise ValueError("Replay already in progress or executed within 24h")

        raw = settings.REDIS_CLIENT.xrange(cls.STREAM_NAME, min=sequence_id, max=sequence_id, count=1)
        if not raw:
            raise ValueError("Task not found")

        _, fields = raw[0]
        # ... dispatch to Celery (unchanged) ...

        # НЕ удаляем запись здесь. Consumer вызовет mark_repaired() при успехе.
        repair_token = f"{sequence_id}:{tz.now().isoformat()}"
        logger.info("DLQ_REPLAY_DISPATCHED", sequence_id=sequence_id, repair_token=repair_token)
        return repair_token

    @classmethod
    def mark_repaired(cls, sequence_id: str, operator_id: str) -> None:
        """
        Explicit human sign-off after confirmed successful repair.
        Only this method deletes the DLQ record.
        """
        settings.REDIS_CLIENT.xdel(cls.STREAM_NAME, sequence_id)
        logger.info(
            "DLQ_RECORD_MARKED_REPAIRED",
            sequence_id=sequence_id,
            operator_id=operator_id,
        )
```

### 3. Repair Protocol — fix Gap B (stuck events)

**Сценарий Gap B:** consumer выставил `acked_at` до crash → повторный replay молча скипается.

**Диагностика:**

```python
# Признаки stuck event:
# ProcessedEvent.acked_at IS NOT NULL  (система считает обработанным)
# НО бизнес-действие не было выполнено (audit log / domain state не совпадают)
```

**Repair через `RepairService`:**

```python
class ConsumerRepairService:

    @staticmethod
    def reset_for_repair(
        outbox_record_id: uuid.UUID,
        consumer: str,
        operator_id: str,
        reason: str,
    ) -> None:
        """
        Reset ProcessedEvent to allow re-processing of a stuck event.

        Safety contract:
        - Only allowed if OutboxRecord has failed_permanently_at IS NOT NULL
          OR record originates from DLQ (verified by caller).
        - Audit log entry is mandatory — this is an irreversible action.
        - After reset, caller MUST immediately dispatch the repair task.
          A reset without subsequent dispatch leaves the event in limbo.
        """
        record = OutboxRecord.objects.filter(id=outbox_record_id).first()
        if record is None:
            raise ValueError(f"OutboxRecord {outbox_record_id} not found or archived")
        if record.failed_permanently_at is None:
            raise ValueError(
                "reset_for_repair is only allowed for quarantine-class records "
                "(failed_permanently_at IS NOT NULL). Use DLQ replay for consumer failures."
            )

        deleted, _ = ProcessedEvent.objects.filter(
            outbox_record_id=outbox_record_id,
            consumer=consumer,
        ).delete()

        logger.warning(
            "consumer_event_reset_for_repair",
            outbox_record_id=str(outbox_record_id),
            consumer=consumer,
            operator_id=operator_id,
            reason=reason,
            processed_events_deleted=deleted,
        )
```

Repair flow (полный):

```
1. ops: диагностирует stuck event (acked_at=not null, но business action отсутствует)
2. ops: вызывает ConsumerRepairService.reset_for_repair(outbox_record_id, consumer, operator_id, reason)
3. ops: повторно диспатчит Celery task с outbox_record_id
4. consumer: claim() → True (ProcessedEvent удалён) → выполняет business action → ack()
5. ops: если Finance domain — верифицирует результат, затем mark_repaired() в DLQ
```

### 4. AggregateSequenceFence при repair

При reset `ProcessedEvent` для события с `aggregate_seq < fence.last_seen_seq` — событие
будет отброшено Mode B fence при следующей обработке. Для repair events это нежелательно.

Если repair требует обхода fence (snapshot domain):

```python
# ConsumerRepairService.reset_for_repair() опционально сбрасывает fence:
if reset_fence:
    AggregateSequenceFence.objects.filter(
        tenant_id=..., aggregate_type=..., aggregate_id=..., consumer=consumer,
    ).update(last_seen_seq=max(0, target_seq - 1))
```

Fence reset для Finance (Mode A) не нужен — ordering обеспечивается serial dispatch.

### 5. ProcessedEvent & Fence retention

`ProcessedEvent` и `AggregateSequenceFence` не имеют retention policy в этом ADR.
Они растут O(events) и O(aggregates) соответственно. Это принято как трейдофф:
очистка — scope отдельного ADR (cross-domain consistency repair, ADR-0014 pending).

### Итоговая Recovery Boundary Matrix

| Сценарий | Recovery возможна? | Источник |
|---|---|---|
| Consumer crash, lease не истёк | Да (ADR-0011 reclaim) | `ProcessedEvent.lease_expires_at` |
| Consumer crash, lease истёк | Да (ADR-0011 reclaim) | `ProcessedEvent.lease_expires_at` |
| Publish failure (dispatcher) | Да, в пределах retention window | `OutboxRecord.failed_permanently_at` + repair |
| Consumer failure (DLQ) | Да, до explicit mark_repaired() | `infrastructure:dlq:stream` |
| Gap B: stuck acked event | Да, через ConsumerRepairService | `ProcessedEvent` reset + re-dispatch |
| Replay за пределами retention window | **Нет** | domain state = canonical |
| Full consumer state rebuild с нуля | **Нет** (не в scope) | требует event store (не ADR-0013) |

---

## Review Date

Пересмотреть при:
- P99 `OutboxRecord` accumulation rate превышает 1M records/день → пересмотреть `OUTBOX_ARCHIVE_WINDOW_DAYS`.
- Запросах аудита на историю событий старше retention window → требуется отдельный event log ADR.
- Реализации cross-domain consistency repair → `ProcessedEvent` cleanup станет частью reconciliation protocol.

---

## Связанные ADR

- [ADR-0010](0010-transactional-outbox-delivery-guarantees.md) — delivery guarantees; retention contract теперь явный
- [ADR-0011](0011-consumer-lease-reclaim-protocol.md) — reclaim covers transport-layer recovery; repair covers application-layer stuck events
- [ADR-0012](0012-hybrid-event-ordering-strategy.md) — AggregateSequenceFence bypass при repair через `reset_fence` флаг
- [ADR-0002](0002-zero-downtime-migration-governance.md) — Expand & Contract для `common_outbox_archive` таблицы
- ADR-0014 (pending) — Cross-domain consistency repair (Finance ↔ Contracts reconciliation)
