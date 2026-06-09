from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import ClassVar


@dataclass(slots=True, frozen=True)
class BankStatementLineMatchedEvent:
    EVENT_TYPE: ClassVar[str] = "payments.BankStatementLineMatched"

    event_id: uuid.UUID
    event_version: int
    occurred_at: datetime
    correlation_id: uuid.UUID
    tenant_id: uuid.UUID
    line_id: uuid.UUID
    statement_id: uuid.UUID
    transaction_id: uuid.UUID | None
    match_status: str
    match_confidence: int
    confirmed_by: uuid.UUID
    previous_status: str

    @classmethod
    def create(
        cls,
        *,
        correlation_id: uuid.UUID,
        tenant_id: uuid.UUID,
        line_id: uuid.UUID,
        statement_id: uuid.UUID,
        transaction_id: uuid.UUID | None,
        match_status: str,
        match_confidence: int,
        confirmed_by: uuid.UUID,
        previous_status: str,
    ) -> "BankStatementLineMatchedEvent":
        return cls(
            event_id=uuid.uuid4(),
            event_version=1,
            occurred_at=datetime.now(tz=timezone.utc),
            correlation_id=correlation_id,
            tenant_id=tenant_id,
            line_id=line_id,
            statement_id=statement_id,
            transaction_id=transaction_id,
            match_status=match_status,
            match_confidence=match_confidence,
            confirmed_by=confirmed_by,
            previous_status=previous_status,
        )
