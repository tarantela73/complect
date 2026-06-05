from apps.common.events.contracts import ContractActivatedEvent, ContractClosedEvent
from apps.common.events.files import FileQuarantinedEvent
from apps.common.events.finance import TransactionPostedEvent

from .base import BaseNotificationHandler, NotificationContext
from .contract_handlers import ContractActivatedHandler, ContractClosedHandler
from .file_handlers import FileQuarantinedHandler
from .finance_handlers import TransactionPostedHandler

NOTIFICATION_ROUTES: dict[str, BaseNotificationHandler] = {
    ContractActivatedEvent.EVENT_TYPE: ContractActivatedHandler(),
    ContractClosedEvent.EVENT_TYPE: ContractClosedHandler(),
    FileQuarantinedEvent.EVENT_TYPE: FileQuarantinedHandler(),
    TransactionPostedEvent.EVENT_TYPE: TransactionPostedHandler(),
}

__all__ = [
    "BaseNotificationHandler",
    "NotificationContext",
    "NOTIFICATION_ROUTES",
]
