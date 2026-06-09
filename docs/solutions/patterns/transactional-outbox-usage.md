---
title: "Transactional Outbox: how to emit domain events"
date: 2026-06-09
category: patterns
module: common
problem_type: architecture_pattern
component: outbox
applies_when:
  - Adding a new domain event to any service
  - "Ensuring event is never lost even if downstream (Redis/Celery) is unavailable"
  - Implementing audit trail for state transitions
tags:
  - outbox
  - events
  - celery
  - transactions
  - at-least-once
---

# Transactional Outbox: how to emit domain events

## Context

Доменные события должны отправляться в Redis/Celery атомарно с мутацией БД. Если сначала закоммитить в БД, потом опубликовать в Redis — в окне между ними может произойти сбой и событие потеряется. Если сначала опубликовать — возможна публикация несуществующего состояния при rollback.

Паттерн решает это через таблицу `common_outbox_records` внутри той же транзакции.

## Guidance

**Шаг 1. Определить событие как `@dataclass(slots=True, frozen=True)` с обязательными полями:**

```python
# apps/your_domain/events.py
from __future__ import annotations
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import ClassVar

@dataclass(slots=True, frozen=True)
class YourDomainEvent:
    EVENT_TYPE: ClassVar[str] = "your_domain.YourDomainEvent"

    event_id: uuid.UUID
    event_version: int
    occurred_at: datetime
    correlation_id: uuid.UUID
    tenant_id: uuid.UUID
    # ... domain fields

    @classmethod
    def create(cls, *, correlation_id: uuid.UUID, tenant_id: uuid.UUID, ...) -> "YourDomainEvent":
        return cls(
            event_id=uuid.uuid4(),
            event_version=1,
            occurred_at=datetime.now(tz=timezone.utc),
            correlation_id=correlation_id,
            tenant_id=tenant_id,
            ...
        )
```

**Шаг 2. В сервисе: всегда внутри `transaction.atomic()`, `push_to_outbox` — после мутации:**

```python
from django.db import transaction
from apps.common.services.outbox_service import TransactionalOutboxService

with transaction.atomic():
    # 1. мутация БД
    MyModel.objects.filter(id=obj.id, tenant_id=tenant_id).update(status=NEW_STATUS)
    obj.refresh_from_db()

    # 2. событие в Outbox — внутри той же транзакции
    TransactionalOutboxService.push_to_outbox(
        YourDomainEvent.create(
            correlation_id=get_correlation_id(),
            tenant_id=tenant_id,
            ...
        )
    )
# Redis-публикация срабатывает через transaction.on_commit() — уже после успешного коммита
```

**Что нельзя делать:**

```python
# ❌ push_to_outbox вне transaction.atomic()
TransactionalOutboxService.push_to_outbox(event)  # нет гарантии атомарности

# ❌ publish напрямую в Redis/Celery вместо Outbox
some_task.delay(...)  # потеряется при сбое до коммита БД

# ❌ push_to_outbox до мутации внутри atomic()
with transaction.atomic():
    TransactionalOutboxService.push_to_outbox(event)  # событие без изменённого состояния
    MyModel.objects.filter(...).update(...)
```

## Why This Matters

`push_to_outbox` пишет в `common_outbox_records` в той же транзакции. Redis-пробуждение (`apply_async`) вызывается через `transaction.on_commit()` — это Django-гарантия: колбэк вызывается только после успешного `COMMIT`. При rollback ни запись в таблице, ни Redis-сигнал не создаются. Диспетчер в Celery (queue `outbox`) читает таблицу и делает at-least-once доставку.

## When to Apply

- Любой переход состояния, который должен быть виден другим доменам (reconciliation, notifications, webhooks)
- Когда тест должен проверить факт отправки события — `OutboxRecord.objects.filter(event_type=...).count()`

## Examples

Рабочий пример из `ManualMatchService.attach()`:

```python
# backend/apps/payments/services/manual_match_service.py
with transaction.atomic():
    BankStatementLine.objects.filter(id=line.id, tenant_id=tenant_id).update(
        match_status=MatchStatus.MATCHED,
        match_confidence=100,
        transaction_id=transaction_id,
    )
    line.refresh_from_db()
    TransactionalOutboxService.push_to_outbox(
        BankStatementLineMatchedEvent.create(
            correlation_id=_safe_correlation_id(),
            tenant_id=tenant_id,
            line_id=line.id,
            statement_id=line.statement_id,
            transaction_id=transaction_id,
            match_status=MatchStatus.MATCHED,
            match_confidence=100,
            confirmed_by=confirmed_by,
            previous_status=previous_status,
        )
    )
```

Проверка в тесте:

```python
ManualMatchService.attach(...)
record = OutboxRecord.objects.filter(event_type=BankStatementLineMatchedEvent.EVENT_TYPE).last()
assert record is not None
assert record.payload_json["match_status"] == "MATCHED"
```
