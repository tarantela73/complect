from apps.common.events.contracts import (
    ContractActivatedEvent,
    ContractClosedEvent,
    ContractCreatedEvent,
)
from apps.common.events.files import FileAvailableEvent, FileQuarantinedEvent, FileUploadedEvent
from apps.common.events.finance import TransactionPostedEvent
from apps.common.events.slo import SLOStateChangedEvent
from apps.common.events.tasks import TaskAssignedEvent, TaskStatusChangedEvent

# Canonical set of subscribable domain event types.
# Webhook subscriptions can only reference types listed here.
# When a new domain event is added, register it here to make it subscribable.
ALL_DOMAIN_EVENT_TYPES: frozenset[str] = frozenset({
    ContractCreatedEvent.EVENT_TYPE,
    ContractActivatedEvent.EVENT_TYPE,
    ContractClosedEvent.EVENT_TYPE,
    TransactionPostedEvent.EVENT_TYPE,
    FileUploadedEvent.EVENT_TYPE,
    FileAvailableEvent.EVENT_TYPE,
    FileQuarantinedEvent.EVENT_TYPE,
    SLOStateChangedEvent.EVENT_TYPE,
    TaskAssignedEvent.EVENT_TYPE,
    TaskStatusChangedEvent.EVENT_TYPE,
})
