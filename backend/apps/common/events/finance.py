import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import ClassVar


@dataclass(slots=True, frozen=True)
class TransactionPostedEvent:
    EVENT_TYPE: ClassVar[str] = "finance.TransactionPosted"

    event_id: uuid.UUID
    correlation_id: uuid.UUID
    occurred_at: datetime
    contract_id: uuid.UUID
    transaction_id: uuid.UUID
    amount: int
    transaction_type: str
    event_version: int = 1

    @classmethod
    def create(
        cls,
        correlation_id: uuid.UUID,
        contract_id: uuid.UUID,
        transaction_id: uuid.UUID,
        amount: int,
        transaction_type: str,
    ) -> "TransactionPostedEvent":
        return cls(
            event_id=uuid.uuid4(),
            correlation_id=correlation_id,
            occurred_at=datetime.now(tz=timezone.utc),
            contract_id=contract_id,
            transaction_id=transaction_id,
            amount=amount,
            transaction_type=transaction_type,
        )
