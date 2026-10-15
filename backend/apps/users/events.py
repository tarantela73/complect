import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class IdentityMergeAuthorityChangedEvent:
    EVENT_TYPE: ClassVar[str] = "users.IdentityMergeAuthorityChanged"

    event_id: uuid.UUID
    event_version: int
    occurred_at: datetime
    correlation_id: uuid.UUID
    operation_id: uuid.UUID
    action_kind: str
    operation_version: int
