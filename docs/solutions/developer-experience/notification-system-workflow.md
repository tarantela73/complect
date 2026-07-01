---
title: "Notification System Workflow: Event-Driven In-App Notifications via Transactional Outbox"
date: "2026-07-01"
category: developer-experience
module: apps/notifications
problem_type: architecture_guide
component: notifications
severity: info
applies_when:
  - "Adding new notification types for domain events"
  - "Debugging missing or duplicate notifications"
  - "Understanding how notifications reach the frontend"
  - "Extending the notification system with new channels"
tags:
  - notifications
  - outbox
  - events
  - consumer
  - websocket
  - in-app
  - domain-events
---

# Notification System Workflow

## Overview

Notifications are event-driven via the Transactional Outbox pattern. There is no direct
`NotificationService.create()` call — all notifications flow through domain events.

## Pipeline

```
Domain mutation (e.g. task status change)
  → transaction.on_commit()
    → push_to_outbox(DomainEvent)
      → OutboxRecord created in same DB transaction
        → on_commit: Celery task wakes outbox dispatcher
          → outbox dispatcher publishes to Redis Stream
            → NotificationsConsumer polls Redis Stream (every 10s)
              → Handler extracts recipients, title, body
                → InAppNotification.objects.create()
                → push_notification_ws() via on_commit
                  → WebSocket broadcast to tenant group
```

## Components

### 1. Domain Events (`apps/common/events/`)

Frozen dataclasses with `EVENT_TYPE` class variable. Each event carries payload needed
by the notification handler.

```python
@dataclass(slots=True, frozen=True)
class TaskStatusChangedEvent:
    EVENT_TYPE: ClassVar[str] = "tasks.TaskStatusChanged"
    event_id: uuid.UUID
    correlation_id: uuid.UUID
    occurred_at: datetime
    task_id: uuid.UUID
    tenant_id: uuid.UUID
    task_title: str
    old_status: str
    new_status: str
    changed_by_id: uuid.UUID
    creator_id: uuid.UUID
    assigned_to_id: Optional[uuid.UUID]
```

### 2. Outbox Publication (`apps/common/services/outbox_service.py`)

Events are published inside `transaction.atomic()` blocks, strictly after the state
mutation and before commit:

```python
with transaction.atomic():
    saved = TaskRepository.save(task)
    TransactionalOutboxService.push_to_outbox(
        TaskStatusChangedEvent.create(
            correlation_id=uuid.uuid4(),
            task_id=saved.id,
            tenant_id=saved.tenant_id,
            ...
        )
    )
```

### 3. Event Registry (`apps/common/events/registry.py`)

All subscribable event types must be registered in `ALL_DOMAIN_EVENT_TYPES` frozenset.
This enables webhook subscriptions for the event.

### 4. Notification Handlers (`apps/notifications/handlers/`)

Each event type has a handler that determines recipients, title, and body:

```python
class TaskStatusChangedHandler(BaseNotificationHandler):
    default_in_app_enabled = True

    def get_recipient_ids(self, ctx: NotificationContext) -> list[uuid.UUID]:
        # Returns creator + assigned_to, excluding the actor
        ...

    def get_title(self, ctx: NotificationContext) -> str:
        return f"Статус задачи: {_STATUS_LABELS[ctx.payload['new_status']]}"

    def get_body(self, ctx: NotificationContext) -> str:
        return f"Задача «{title}»: {old} → {new}"
```

### 5. Notification Routes (`apps/notifications/handlers/__init__.py`)

Maps event types to handler instances:

```python
NOTIFICATION_ROUTES: dict[str, BaseNotificationHandler] = {
    TaskAssignedEvent.EVENT_TYPE: TaskAssignedHandler(),
    TaskStatusChangedEvent.EVENT_TYPE: TaskStatusChangedHandler(),
    TaskCompletedEvent.EVENT_TYPE: TaskCompletedHandler(),
    TaskCancelledEvent.EVENT_TYPE: TaskCancelledHandler(),
    ...
}
```

### 6. Consumer (`apps/notifications/consumer/notifications_consumer.py`)

`NotificationsConsumer` extends `BaseDomainConsumer` with `FENCED` ordering mode.
Runs every 10 seconds via Celery Beat on the `notifications` queue.

For each event:
1. Looks up handler from `NOTIFICATION_ROUTES`
2. Calls `handler.get_recipient_ids(ctx)` to determine recipients
3. For each recipient: creates `InAppNotification`, pushes WebSocket, optionally sends email

### 7. WebSocket Push (`apps/notifications/channels/in_app.py`)

`push_notification_ws()` sends a `group_send` to `notifications.tenant.{tenant_id}`.
The `RealtimeConsumer` broadcasts `{"event_type": "notification.new", "payload": {...}}`
to the client. Frontend `useNotificationSocket()` hook invalidates the unread count cache.

## Adding a New Notification Type

1. **Create event** in `apps/common/events/<domain>.py`:
   - Frozen dataclass with `EVENT_TYPE` class var
   - `create()` classmethod that generates `event_id`, `correlation_id`, `occurred_at`

2. **Register event** in `apps/common/events/registry.py`:
   - Add to `ALL_DOMAIN_EVENT_TYPES` frozenset

3. **Create handler** in `apps/notifications/handlers/<domain>_handlers.py`:
   - Extend `BaseNotificationHandler`
   - Implement `get_recipient_ids()`, `get_title()`, `get_body()`
   - Set `default_in_app_enabled` and `default_email_enabled`

4. **Register route** in `apps/notifications/handlers/__init__.py`:
   - Import event and handler
   - Add to `NOTIFICATION_ROUTES` dict

5. **Publish event** in the service layer:
   - Call `TransactionalOutboxService.push_to_outbox()` inside `transaction.atomic()`

## Task Notification Events

| Event | Recipients | Title |
|-------|-----------|-------|
| `tasks.TaskAssigned` | Newly assigned user | «Назначена задача» |
| `tasks.TaskStatusChanged` | Creator + assignee (excl. actor) | «Статус задачи: {status}» |
| `tasks.TaskCompleted` | Creator + assignee | «Задача выполнена» |
| `tasks.TaskCancelled` | Creator + assignee | «Задача отменена» |

## Debugging

**Notification not arriving:**
1. Check `OutboxRecord` table — is the event persisted?
2. Check Celery worker on `notifications` queue — is it running?
3. Check `ProcessedEvent` table — was the event already processed (idempotency)?
4. Check `NotificationPreference` — is `in_app_enabled` true for the user/event_type?

**Duplicate notifications:**
- Consumer uses idempotency via `ProcessedEvent` table. Duplicates should not occur.
- If they do, check `claim`/`reclaim` logic in `BaseDomainConsumer`.

**WebSocket not receiving:**
- Check `RealtimeConsumer` connection to `notifications.tenant.{tenant_id}` group
- Frontend `useNotificationSocket()` polls every 30s as fallback

## Related

- `apps/common/events/tasks.py` — domain events for tasks
- `apps/common/services/outbox_service.py` — `TransactionalOutboxService.push_to_outbox()`
- `apps/common/services/consumer_base.py` — `BaseDomainConsumer` with idempotency/fencing
- `apps/notifications/consumer/notifications_consumer.py` — consumer logic
- `apps/notifications/handlers/` — all notification handlers
- `frontend/src/shared/api/notifications.ts` — frontend hooks
- `frontend/src/app/dashboard/notifications/page.tsx` — notifications page
