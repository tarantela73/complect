from apps.common.events.contracts import ContractActivatedEvent, ContractClosedEvent
from apps.common.events.files import FileQuarantinedEvent
from apps.common.events.finance import TransactionPostedEvent
from apps.common.events.slo import SLOStateChangedEvent

from .base import BaseNotificationHandler, NotificationContext
from .contract_handlers import ContractActivatedHandler, ContractClosedHandler
from .file_handlers import FileQuarantinedHandler
from .finance_handlers import TransactionPostedHandler
from .slo_handlers import SLOStateChangedHandler

NOTIFICATION_ROUTES: dict[str, BaseNotificationHandler] = {
    ContractActivatedEvent.EVENT_TYPE: ContractActivatedHandler(),
    ContractClosedEvent.EVENT_TYPE: ContractClosedHandler(),
    FileQuarantinedEvent.EVENT_TYPE: FileQuarantinedHandler(),
    TransactionPostedEvent.EVENT_TYPE: TransactionPostedHandler(),
    SLOStateChangedEvent.EVENT_TYPE: SLOStateChangedHandler(),
}

__all__ = [
    "BaseNotificationHandler",
    "NotificationContext",
    "NOTIFICATION_ROUTES",
]
