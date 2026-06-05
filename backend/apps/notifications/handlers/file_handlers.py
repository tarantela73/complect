import uuid

from apps.notifications.handlers.base import BaseNotificationHandler, NotificationContext

_REASON_LABELS = {
    "virus_detected": "обнаружен вирус",
    "scan_error": "ошибка сканирования",
    "scan_deferred_cb_open": "сканирование отложено",
}


class FileQuarantinedHandler(BaseNotificationHandler):
    default_email_enabled = True
    default_in_app_enabled = True

    def get_recipient_ids(self, ctx: NotificationContext) -> list[uuid.UUID]:
        uid_str = ctx.payload.get("uploaded_by")
        if uid_str:
            try:
                return [uuid.UUID(str(uid_str))]
            except (ValueError, AttributeError):
                pass
        return []

    def get_title(self, ctx: NotificationContext) -> str:
        return "Файл помещён в карантин"

    def get_body(self, ctx: NotificationContext) -> str:
        file_id = str(ctx.payload.get("file_id", ""))
        reason_key = ctx.payload.get("quarantine_reason", "")
        reason = _REASON_LABELS.get(reason_key, reason_key)
        return f"Файл {file_id[:8]}… помещён в карантин. Причина: {reason}."
