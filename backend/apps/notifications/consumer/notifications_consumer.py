from __future__ import annotations

import uuid

import structlog
from django.db import IntegrityError, OperationalError, transaction
from redis.exceptions import RedisError

from apps.common.services.consumer_base import BaseDomainConsumer, StreamEvent
from apps.common.tasks import BaseEnterpriseTask
from apps.notifications.channels.email_channel import send_notification_email_task
from apps.notifications.channels.in_app import push_notification_ws
from apps.notifications.handlers import NOTIFICATION_ROUTES, NotificationContext
from apps.notifications.models import InAppNotification, NotificationPreference
from config.celery import app

logger = structlog.get_logger(__name__)


def _get_or_create_prefs(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    event_type: str,
    default_email: bool,
    default_in_app: bool,
) -> NotificationPreference:
    pref, _ = NotificationPreference.objects.get_or_create(
        tenant_id=tenant_id,
        user_id=user_id,
        event_type=event_type,
        defaults={
            "email_enabled": default_email,
            "in_app_enabled": default_in_app,
        },
    )
    return pref


class NotificationsConsumer(BaseDomainConsumer):
    consumer_name = "notifications.consumer"
    ordering_mode = "FENCED"
    handled_event_types = frozenset(NOTIFICATION_ROUTES.keys())

    def process(self, event: StreamEvent) -> None:
        handler = NOTIFICATION_ROUTES.get(event.event_type)
        if handler is None:
            return

        ctx = NotificationContext(
            event_type=event.event_type,
            tenant_id=event.tenant_id,
            payload=event.payload,
        )

        recipient_ids = handler.get_recipient_ids(ctx)
        if not recipient_ids:
            logger.debug(
                "notification_no_recipients",
                event_type=event.event_type,
                tenant_id=str(event.tenant_id),
            )
            return

        title = handler.get_title(ctx)
        body = handler.get_body(ctx)

        with transaction.atomic():
            for user_id in recipient_ids:
                prefs = _get_or_create_prefs(
                    tenant_id=event.tenant_id,
                    user_id=user_id,
                    event_type=event.event_type,
                    default_email=handler.default_email_enabled,
                    default_in_app=handler.default_in_app_enabled,
                )

                if prefs.in_app_enabled:
                    notif = InAppNotification.objects.create(
                        tenant_id=event.tenant_id,
                        user_id=user_id,
                        title=title,
                        body=body,
                        event_type=event.event_type,
                        payload=event.payload,
                    )
                    _uid, _tid, _nid = user_id, event.tenant_id, notif.id
                    transaction.on_commit(lambda uid=_uid, tid=_tid, nid=_nid: push_notification_ws(uid, tid, nid))

                if prefs.email_enabled:
                    _uid_str = str(user_id)
                    transaction.on_commit(
                        lambda uid=_uid_str, t=title, b=body: send_notification_email_task.delay(
                            user_id=uid,
                            subject=t,
                            body=b,
                        )
                    )


@app.task(
    base=BaseEnterpriseTask,
    name="apps.notifications.consumer.notifications_consumer.run_notifications_consumer_task",
    queue="notifications",
    ignore_result=True,
    autoretry_for=(OperationalError, RedisError, IntegrityError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def run_notifications_consumer_task() -> None:
    NotificationsConsumer().run_batch(100)
