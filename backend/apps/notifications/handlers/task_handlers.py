import uuid

from apps.notifications.handlers.base import BaseNotificationHandler, NotificationContext


class TaskCompletedHandler(BaseNotificationHandler):
    default_in_app_enabled = True

    def get_recipient_ids(self, ctx: NotificationContext) -> list[uuid.UUID]:
        recipients = []
        for key in ("creator_id", "assigned_to_id"):
            uid_str = ctx.payload.get(key)
            if uid_str:
                try:
                    recipients.append(uuid.UUID(str(uid_str)))
                except (ValueError, AttributeError):
                    pass
        return list(dict.fromkeys(recipients))  # deduplicate, preserve order

    def get_title(self, ctx: NotificationContext) -> str:
        return "Задача выполнена"

    def get_body(self, ctx: NotificationContext) -> str:
        title = ctx.payload.get("task_title", "")
        return f"Задача «{title}» отмечена как выполненная."


class TaskCancelledHandler(BaseNotificationHandler):
    default_in_app_enabled = True

    def get_recipient_ids(self, ctx: NotificationContext) -> list[uuid.UUID]:
        recipients = []
        for key in ("creator_id", "assigned_to_id"):
            uid_str = ctx.payload.get(key)
            if uid_str:
                try:
                    recipients.append(uuid.UUID(str(uid_str)))
                except (ValueError, AttributeError):
                    pass
        return list(dict.fromkeys(recipients))

    def get_title(self, ctx: NotificationContext) -> str:
        return "Задача отменена"

    def get_body(self, ctx: NotificationContext) -> str:
        title = ctx.payload.get("task_title", "")
        return f"Задача «{title}» была отменена."
