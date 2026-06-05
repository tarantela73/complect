import uuid

from apps.notifications.handlers.base import BaseNotificationHandler, NotificationContext

_TYPE_LABELS = {
    "PAYMENT": "Оплата",
    "REFUND": "Возврат",
    "CORRECTION": "Корректировка",
}


class TransactionPostedHandler(BaseNotificationHandler):
    default_email_enabled = False
    default_in_app_enabled = True

    def get_recipient_ids(self, ctx: NotificationContext) -> list[uuid.UUID]:
        uid_str = ctx.payload.get("posted_by")
        if uid_str:
            try:
                return [uuid.UUID(str(uid_str))]
            except (ValueError, AttributeError):
                pass
        return []

    def get_title(self, ctx: NotificationContext) -> str:
        tx_type = _TYPE_LABELS.get(ctx.payload.get("transaction_type", ""), "Транзакция")
        return f"{tx_type} проведена"

    def get_body(self, ctx: NotificationContext) -> str:
        amount = ctx.payload.get("amount", 0)
        rub = int(amount) / 100
        contract_id = str(ctx.payload.get("contract_id", ""))
        tx_type = _TYPE_LABELS.get(ctx.payload.get("transaction_type", ""), "Транзакция")
        return f"{tx_type}: {rub:,.2f} ₽ по договору {contract_id[:8]}…"
