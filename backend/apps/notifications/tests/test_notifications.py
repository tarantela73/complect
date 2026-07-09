"""
Tests for the Notifications domain.

Coverage:
  - NotificationsConsumer: in-app delivery, email dispatch, preference defaults, no recipients
  - API: list, unread-count, mark-read, read-all (tenant isolation, RBAC)
  - Handler registry: correct recipient extraction for each event type
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from django.utils import timezone as django_timezone

from apps.common.models.outbox import OutboxRecord
from apps.common.services.consumer_base import StreamEvent
from apps.notifications.consumer.notifications_consumer import NotificationsConsumer
from apps.notifications.handlers import NOTIFICATION_ROUTES, NotificationContext
from apps.notifications.models import InAppNotification, NotificationPreference


def _make_stream_event(
    event_type: str,
    payload: dict,
    tenant_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
) -> StreamEvent:
    tid = tenant_id or uuid.uuid4()
    return StreamEvent(
        outbox_record_id=uuid.uuid4(),
        causal_root_id=None,
        correlation_id=correlation_id or uuid.uuid4(),
        tenant_id=tid,
        aggregate_id=None,
        aggregate_type=None,
        aggregate_seq=None,
        event_type=event_type,
        payload={**payload, "tenant_id": str(tid)},
    )


# ──────────────────────────────────────────────────────────────────────────────
# Handler registry
# ──────────────────────────────────────────────────────────────────────────────


class TestHandlerRegistry:
    def test_all_event_types_registered(self):
        from apps.common.events.contracts import ContractActivatedEvent, ContractClosedEvent
        from apps.common.events.files import FileQuarantinedEvent
        from apps.common.events.finance import TransactionPostedEvent
        from apps.common.events.tasks import TaskCancelledEvent, TaskCompletedEvent

        assert ContractActivatedEvent.EVENT_TYPE in NOTIFICATION_ROUTES
        assert ContractClosedEvent.EVENT_TYPE in NOTIFICATION_ROUTES
        assert FileQuarantinedEvent.EVENT_TYPE in NOTIFICATION_ROUTES
        assert TransactionPostedEvent.EVENT_TYPE in NOTIFICATION_ROUTES
        assert TaskCompletedEvent.EVENT_TYPE in NOTIFICATION_ROUTES
        assert TaskCancelledEvent.EVENT_TYPE in NOTIFICATION_ROUTES

    def test_contract_activated_recipient_from_user_id(self):
        from apps.common.events.contracts import ContractActivatedEvent

        user_id = uuid.uuid4()
        handler = NOTIFICATION_ROUTES[ContractActivatedEvent.EVENT_TYPE]
        ctx = NotificationContext(
            event_type=ContractActivatedEvent.EVENT_TYPE,
            tenant_id=uuid.uuid4(),
            payload={"user_id": str(user_id), "contract_id": str(uuid.uuid4())},
        )
        assert handler.get_recipient_ids(ctx) == [user_id]

    def test_file_quarantined_recipient_from_uploaded_by(self):
        from apps.common.events.files import FileQuarantinedEvent

        uploader_id = uuid.uuid4()
        handler = NOTIFICATION_ROUTES[FileQuarantinedEvent.EVENT_TYPE]
        ctx = NotificationContext(
            event_type=FileQuarantinedEvent.EVENT_TYPE,
            tenant_id=uuid.uuid4(),
            payload={"uploaded_by": str(uploader_id), "file_id": str(uuid.uuid4())},
        )
        assert handler.get_recipient_ids(ctx) == [uploader_id]

    def test_transaction_posted_recipient_from_posted_by(self):
        from apps.common.events.finance import TransactionPostedEvent

        poster_id = uuid.uuid4()
        handler = NOTIFICATION_ROUTES[TransactionPostedEvent.EVENT_TYPE]
        ctx = NotificationContext(
            event_type=TransactionPostedEvent.EVENT_TYPE,
            tenant_id=uuid.uuid4(),
            payload={"posted_by": str(poster_id), "contract_id": str(uuid.uuid4()), "amount": 10000},
        )
        assert handler.get_recipient_ids(ctx) == [poster_id]

    def test_handler_returns_empty_list_when_no_recipient_field(self):
        from apps.common.events.files import FileQuarantinedEvent

        handler = NOTIFICATION_ROUTES[FileQuarantinedEvent.EVENT_TYPE]
        ctx = NotificationContext(
            event_type=FileQuarantinedEvent.EVENT_TYPE,
            tenant_id=uuid.uuid4(),
            payload={"file_id": str(uuid.uuid4())},
        )
        assert handler.get_recipient_ids(ctx) == []


# ──────────────────────────────────────────────────────────────────────────────
# NotificationsConsumer
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestNotificationsConsumer:
    def _make_outbox_record(self, event_type: str, payload: dict, tenant_id: uuid.UUID) -> OutboxRecord:
        full_payload = {**payload, "tenant_id": str(tenant_id)}
        return OutboxRecord.objects.create(
            event_type=event_type,
            payload_json=full_payload,
            correlation_id=uuid.uuid4(),
        )

    def test_in_app_notification_created_on_contract_activated(self):
        from apps.common.events.contracts import ContractActivatedEvent

        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        payload = {
            "user_id": str(user_id),
            "contract_id": str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()),
            "event_id": str(uuid.uuid4()),
            "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
            "event_version": 1,
        }

        with (
            patch("apps.notifications.consumer.notifications_consumer.push_notification_ws"),
            patch("apps.notifications.consumer.notifications_consumer.send_notification_email_task"),
        ):
            consumer = NotificationsConsumer()
            event = _make_stream_event(
                ContractActivatedEvent.EVENT_TYPE,
                payload,
                tenant_id=tenant_id,
            )
            consumer.process(event)

        notif = InAppNotification.objects.filter(tenant_id=tenant_id, user_id=user_id).first()
        assert notif is not None
        assert notif.event_type == ContractActivatedEvent.EVENT_TYPE
        assert notif.read_at is None

    def test_preference_defaults_applied(self):
        from apps.common.events.contracts import ContractActivatedEvent

        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        payload = {
            "user_id": str(user_id),
            "contract_id": str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()),
            "event_id": str(uuid.uuid4()),
            "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
            "event_version": 1,
        }

        with (
            patch("apps.notifications.consumer.notifications_consumer.push_notification_ws"),
            patch("apps.notifications.consumer.notifications_consumer.send_notification_email_task"),
        ):
            consumer = NotificationsConsumer()
            event = _make_stream_event(
                ContractActivatedEvent.EVENT_TYPE,
                payload,
                tenant_id=tenant_id,
            )
            consumer.process(event)

        pref = NotificationPreference.objects.get(
            tenant_id=tenant_id,
            user_id=user_id,
            event_type=ContractActivatedEvent.EVENT_TYPE,
        )
        # ContractActivatedHandler default: email=True, in_app=True
        assert pref.in_app_enabled is True
        assert pref.email_enabled is True

    def test_no_notification_when_in_app_disabled(self):
        from apps.common.events.contracts import ContractActivatedEvent

        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()

        NotificationPreference.objects.create(
            tenant_id=tenant_id,
            user_id=user_id,
            event_type=ContractActivatedEvent.EVENT_TYPE,
            in_app_enabled=False,
            email_enabled=False,
        )

        payload = {
            "user_id": str(user_id),
            "contract_id": str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()),
            "event_id": str(uuid.uuid4()),
            "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
            "event_version": 1,
        }

        with (
            patch("apps.notifications.consumer.notifications_consumer.push_notification_ws"),
            patch("apps.notifications.consumer.notifications_consumer.send_notification_email_task"),
        ):
            consumer = NotificationsConsumer()
            event = _make_stream_event(
                ContractActivatedEvent.EVENT_TYPE,
                payload,
                tenant_id=tenant_id,
            )
            consumer.process(event)

        count = InAppNotification.objects.filter(tenant_id=tenant_id, user_id=user_id).count()
        assert count == 0

    def test_no_notification_when_no_recipients(self):
        from apps.common.events.files import FileQuarantinedEvent

        tenant_id = uuid.uuid4()
        payload = {
            "file_id": str(uuid.uuid4()),
            "quarantine_reason": "virus_detected",
            "correlation_id": str(uuid.uuid4()),
            "event_id": str(uuid.uuid4()),
            "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
            "event_version": 1,
        }

        consumer = NotificationsConsumer()
        event = _make_stream_event(FileQuarantinedEvent.EVENT_TYPE, payload, tenant_id=tenant_id)
        consumer.process(event)

        count = InAppNotification.objects.filter(tenant_id=tenant_id).count()
        assert count == 0

    def test_task_completed_notifies_creator(self):
        from apps.common.events.tasks import TaskCompletedEvent

        tenant_id = uuid.uuid4()
        creator_id = uuid.uuid4()
        payload = {
            "task_id": str(uuid.uuid4()),
            "task_title": "Закрыть сделку",
            "creator_id": str(creator_id),
            "assigned_to_id": None,
            "correlation_id": str(uuid.uuid4()),
            "event_id": str(uuid.uuid4()),
            "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
            "event_version": 1,
        }

        with (
            patch("apps.notifications.consumer.notifications_consumer.push_notification_ws"),
            patch("apps.notifications.consumer.notifications_consumer.send_notification_email_task"),
        ):
            consumer = NotificationsConsumer()
            consumer.process(_make_stream_event(TaskCompletedEvent.EVENT_TYPE, payload, tenant_id=tenant_id))

        notif = InAppNotification.objects.filter(tenant_id=tenant_id, user_id=creator_id).first()
        assert notif is not None
        assert "Закрыть сделку" in notif.body

    def test_task_completed_notifies_both_creator_and_assignee(self):
        from apps.common.events.tasks import TaskCompletedEvent

        tenant_id = uuid.uuid4()
        creator_id = uuid.uuid4()
        assignee_id = uuid.uuid4()
        payload = {
            "task_id": str(uuid.uuid4()),
            "task_title": "Проверить отчёт",
            "creator_id": str(creator_id),
            "assigned_to_id": str(assignee_id),
            "correlation_id": str(uuid.uuid4()),
            "event_id": str(uuid.uuid4()),
            "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
            "event_version": 1,
        }

        with (
            patch("apps.notifications.consumer.notifications_consumer.push_notification_ws"),
            patch("apps.notifications.consumer.notifications_consumer.send_notification_email_task"),
        ):
            consumer = NotificationsConsumer()
            consumer.process(_make_stream_event(TaskCompletedEvent.EVENT_TYPE, payload, tenant_id=tenant_id))

        assert InAppNotification.objects.filter(tenant_id=tenant_id, user_id=creator_id).exists()
        assert InAppNotification.objects.filter(tenant_id=tenant_id, user_id=assignee_id).exists()

    def test_task_cancelled_notifies_creator(self):
        from apps.common.events.tasks import TaskCancelledEvent

        tenant_id = uuid.uuid4()
        creator_id = uuid.uuid4()
        payload = {
            "task_id": str(uuid.uuid4()),
            "task_title": "Созвониться с клиентом",
            "creator_id": str(creator_id),
            "assigned_to_id": None,
            "correlation_id": str(uuid.uuid4()),
            "event_id": str(uuid.uuid4()),
            "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
            "event_version": 1,
        }

        with (
            patch("apps.notifications.consumer.notifications_consumer.push_notification_ws"),
            patch("apps.notifications.consumer.notifications_consumer.send_notification_email_task"),
        ):
            consumer = NotificationsConsumer()
            consumer.process(_make_stream_event(TaskCancelledEvent.EVENT_TYPE, payload, tenant_id=tenant_id))

        notif = InAppNotification.objects.filter(tenant_id=tenant_id, user_id=creator_id).first()
        assert notif is not None
        assert "Созвониться с клиентом" in notif.body

    def test_task_completed_deduplicates_when_creator_is_assignee(self):
        from apps.common.events.tasks import TaskCompletedEvent

        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        payload = {
            "task_id": str(uuid.uuid4()),
            "task_title": "Самоназначение",
            "creator_id": str(user_id),
            "assigned_to_id": str(user_id),
            "correlation_id": str(uuid.uuid4()),
            "event_id": str(uuid.uuid4()),
            "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
            "event_version": 1,
        }

        with (
            patch("apps.notifications.consumer.notifications_consumer.push_notification_ws"),
            patch("apps.notifications.consumer.notifications_consumer.send_notification_email_task"),
        ):
            consumer = NotificationsConsumer()
            consumer.process(_make_stream_event(TaskCompletedEvent.EVENT_TYPE, payload, tenant_id=tenant_id))

        count = InAppNotification.objects.filter(tenant_id=tenant_id, user_id=user_id).count()
        assert count == 1


# ──────────────────────────────────────────────────────────────────────────────
# API
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestNotificationsApi:
    def _setup_context(self, tenant_id: uuid.UUID, user_id: uuid.UUID):
        from apps.common.tenancy.context_core import set_context

        class _Ctx:
            pass

        ctx = _Ctx()
        ctx.tenant_id = tenant_id
        ctx.user_id = user_id
        set_context(ctx)
        return ctx

    def test_list_returns_only_current_user_notifications(self, client):

        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        other_user_id = uuid.uuid4()

        InAppNotification.objects.create(
            tenant_id=tenant_id,
            user_id=user_id,
            title="Для меня",
            body="body",
            event_type="contracts.ContractActivated",
        )
        InAppNotification.objects.create(
            tenant_id=tenant_id,
            user_id=other_user_id,
            title="Для другого",
            body="body",
            event_type="contracts.ContractActivated",
        )

        from apps.notifications.queries.notification_queries import get_notifications_page

        page = get_notifications_page(tenant_id=tenant_id, user_id=user_id)
        assert page.count == 1
        assert page.results[0].title == "Для меня"

    def test_unread_count_excludes_read(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()

        InAppNotification.objects.create(
            tenant_id=tenant_id,
            user_id=user_id,
            title="Прочитано",
            body="x",
            event_type="e",
            read_at=django_timezone.now(),
        )
        InAppNotification.objects.create(
            tenant_id=tenant_id,
            user_id=user_id,
            title="Непрочитано",
            body="x",
            event_type="e",
        )

        from apps.notifications.queries.notification_queries import get_unread_count

        assert get_unread_count(tenant_id=tenant_id, user_id=user_id) == 1

    def test_unread_count_tenant_isolated(self):
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        user_id = uuid.uuid4()

        InAppNotification.objects.create(
            tenant_id=tenant_a,
            user_id=user_id,
            title="A",
            body="x",
            event_type="e",
        )
        InAppNotification.objects.create(
            tenant_id=tenant_b,
            user_id=user_id,
            title="B",
            body="x",
            event_type="e",
        )

        from apps.notifications.queries.notification_queries import get_unread_count

        assert get_unread_count(tenant_id=tenant_a, user_id=user_id) == 1
        assert get_unread_count(tenant_id=tenant_b, user_id=user_id) == 1

    def test_cursor_pagination(self):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()

        for i in range(5):
            InAppNotification.objects.create(
                tenant_id=tenant_id,
                user_id=user_id,
                title=f"N{i}",
                body="x",
                event_type="e",
            )

        from apps.notifications.queries.notification_queries import get_notifications_page

        page1 = get_notifications_page(tenant_id=tenant_id, user_id=user_id, limit=3)
        assert len(page1.results) == 3
        assert page1.next_cursor is not None

        page2 = get_notifications_page(
            tenant_id=tenant_id,
            user_id=user_id,
            cursor=page1.next_cursor,
            limit=3,
        )
        assert len(page2.results) == 2


# ──────────────────────────────────────────────────────────────────────────────
# Task notification handlers
# ──────────────────────────────────────────────────────────────────────────────


class TestTaskAssignedHandler:
    def test_recipient_is_assigned_user(self):
        from apps.common.events.tasks import TaskAssignedEvent

        assignee = uuid.uuid4()
        handler = NOTIFICATION_ROUTES[TaskAssignedEvent.EVENT_TYPE]
        ctx = NotificationContext(
            event_type=TaskAssignedEvent.EVENT_TYPE,
            tenant_id=uuid.uuid4(),
            payload={"assigned_to_id": str(assignee), "assigned_by_id": str(uuid.uuid4())},
        )
        assert handler.get_recipient_ids(ctx) == [assignee]

    def test_empty_when_no_assigned_to(self):
        from apps.common.events.tasks import TaskAssignedEvent

        handler = NOTIFICATION_ROUTES[TaskAssignedEvent.EVENT_TYPE]
        ctx = NotificationContext(
            event_type=TaskAssignedEvent.EVENT_TYPE,
            tenant_id=uuid.uuid4(),
            payload={"assigned_by_id": str(uuid.uuid4())},
        )
        assert handler.get_recipient_ids(ctx) == []

    def test_title_and_body(self):
        from apps.common.events.tasks import TaskAssignedEvent

        handler = NOTIFICATION_ROUTES[TaskAssignedEvent.EVENT_TYPE]
        ctx = NotificationContext(
            event_type=TaskAssignedEvent.EVENT_TYPE,
            tenant_id=uuid.uuid4(),
            payload={"task_title": "Fix bug", "assigned_to_id": str(uuid.uuid4())},
        )
        assert handler.get_title(ctx) == "Назначена задача"
        assert "Fix bug" in handler.get_body(ctx)


class TestTaskStatusChangedHandler:
    def test_recipients_exclude_actor(self):
        from apps.common.events.tasks import TaskStatusChangedEvent

        creator = uuid.uuid4()
        assignee = uuid.uuid4()
        actor = uuid.uuid4()  # different from both

        handler = NOTIFICATION_ROUTES[TaskStatusChangedEvent.EVENT_TYPE]
        ctx = NotificationContext(
            event_type=TaskStatusChangedEvent.EVENT_TYPE,
            tenant_id=uuid.uuid4(),
            payload={
                "creator_id": str(creator),
                "assigned_to_id": str(assignee),
                "changed_by_id": str(actor),
            },
        )
        recipients = handler.get_recipient_ids(ctx)
        assert creator in recipients
        assert assignee in recipients
        assert actor not in recipients

    def test_actor_excluded_when_creator(self):
        from apps.common.events.tasks import TaskStatusChangedEvent

        creator = uuid.uuid4()
        assignee = uuid.uuid4()

        handler = NOTIFICATION_ROUTES[TaskStatusChangedEvent.EVENT_TYPE]
        ctx = NotificationContext(
            event_type=TaskStatusChangedEvent.EVENT_TYPE,
            tenant_id=uuid.uuid4(),
            payload={
                "creator_id": str(creator),
                "assigned_to_id": str(assignee),
                "changed_by_id": str(creator),  # creator is the actor
            },
        )
        recipients = handler.get_recipient_ids(ctx)
        assert creator not in recipients
        assert assignee in recipients

    def test_actor_excluded_when_assignee(self):
        from apps.common.events.tasks import TaskStatusChangedEvent

        creator = uuid.uuid4()
        assignee = uuid.uuid4()

        handler = NOTIFICATION_ROUTES[TaskStatusChangedEvent.EVENT_TYPE]
        ctx = NotificationContext(
            event_type=TaskStatusChangedEvent.EVENT_TYPE,
            tenant_id=uuid.uuid4(),
            payload={
                "creator_id": str(creator),
                "assigned_to_id": str(assignee),
                "changed_by_id": str(assignee),  # assignee is the actor
            },
        )
        recipients = handler.get_recipient_ids(ctx)
        assert creator in recipients
        assert assignee not in recipients

    def test_title_shows_new_status(self):
        from apps.common.events.tasks import TaskStatusChangedEvent

        handler = NOTIFICATION_ROUTES[TaskStatusChangedEvent.EVENT_TYPE]
        ctx = NotificationContext(
            event_type=TaskStatusChangedEvent.EVENT_TYPE,
            tenant_id=uuid.uuid4(),
            payload={"new_status": "in_progress"},
        )
        assert "В работе" in handler.get_title(ctx)

    def test_body_shows_transition(self):
        from apps.common.events.tasks import TaskStatusChangedEvent

        handler = NOTIFICATION_ROUTES[TaskStatusChangedEvent.EVENT_TYPE]
        ctx = NotificationContext(
            event_type=TaskStatusChangedEvent.EVENT_TYPE,
            tenant_id=uuid.uuid4(),
            payload={"task_title": "My Task", "old_status": "open", "new_status": "in_progress"},
        )
        body = handler.get_body(ctx)
        assert "My Task" in body
        assert "Открыта" in body
        assert "В работе" in body
