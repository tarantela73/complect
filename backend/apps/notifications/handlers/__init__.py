from apps.common.events.contracts import ContractActivatedEvent, ContractClosedEvent
from apps.common.events.files import FileQuarantinedEvent
from apps.common.events.finance import TransactionPostedEvent
from apps.common.events.slo import SLOStateChangedEvent
from apps.common.events.tasks import TaskCancelledEvent, TaskCompletedEvent

from .base import BaseNotificationHandler, NotificationContext
from .contract_handlers import ContractActivatedHandler, ContractClosedHandler
from .file_handlers import FileQuarantinedHandler
from .finance_handlers import TransactionPostedHandler
from .slo_handlers import SLOStateChangedHandler
from .task_handlers import TaskCancelledHandler, TaskCompletedHandler

NOTIFICATION_ROUTES: dict[str, BaseNotificationHandler] = {
    ContractActivatedEvent.EVENT_TYPE: ContractActivatedHandler(),
    ContractClosedEvent.EVENT_TYPE: ContractClosedHandler(),
    FileQuarantinedEvent.EVENT_TYPE: FileQuarantinedHandler(),
    TransactionPostedEvent.EVENT_TYPE: TransactionPostedHandler(),
    SLOStateChangedEvent.EVENT_TYPE: SLOStateChangedHandler(),
    TaskCompletedEvent.EVENT_TYPE: TaskCompletedHandler(),
    TaskCancelledEvent.EVENT_TYPE: TaskCancelledHandler(),
}

__all__ = [
    "BaseNotificationHandler",
    "NotificationContext",
    "NOTIFICATION_ROUTES",
]
