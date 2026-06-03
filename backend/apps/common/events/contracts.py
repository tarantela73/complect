# backend/apps/common/events/contracts.py
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import ClassVar


@dataclass(slots=True, frozen=True)
class ContractClosedEvent:
    EVENT_TYPE: ClassVar[str] = "contracts.ContractClosed"
    event_id: uuid.UUID
    correlation_id: uuid.UUID
    occurred_at: datetime
    contract_id: uuid.UUID
    user_id: uuid.UUID
    remaining_amount: int
    event_version: int = 1

    @classmethod
    def create(
        cls,
        correlation_id: uuid.UUID,
        contract_id: uuid.UUID,
        user_id: uuid.UUID,
        remaining_amount: int,
    ) -> "ContractClosedEvent":
        return cls(
            event_id=uuid.uuid4(),
            correlation_id=correlation_id,
            occurred_at=datetime.now(tz=timezone.utc),
            contract_id=contract_id,
            user_id=user_id,
            remaining_amount=remaining_amount,
        )
