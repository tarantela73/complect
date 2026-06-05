from apps.notifications.handlers.base import BaseNotificationHandler, NotificationContext


class ContractActivatedHandler(BaseNotificationHandler):
    default_email_enabled = True
    default_in_app_enabled = True

    def get_title(self, ctx: NotificationContext) -> str:
        return "Договор активирован"

    def get_body(self, ctx: NotificationContext) -> str:
        contract_id = str(ctx.payload.get("contract_id", ""))
        return f"Договор {contract_id[:8]}… переведён в статус ACTIVE."


class ContractClosedHandler(BaseNotificationHandler):
    default_email_enabled = False
    default_in_app_enabled = True

    def get_title(self, ctx: NotificationContext) -> str:
        return "Договор закрыт"

    def get_body(self, ctx: NotificationContext) -> str:
        contract_id = str(ctx.payload.get("contract_id", ""))
        return f"Договор {contract_id[:8]}… закрыт."
