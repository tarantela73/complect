from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import ClassVar


@dataclass(slots=True, frozen=True)
class SLOStateChangedEvent:
    EVENT_TYPE: ClassVar[str] = "slo.state_changed"

    event_id: uuid.UUID
    event_version: int
    occurred_at: datetime
    correlation_id: uuid.UUID
    slo_name: str
    slo_label: str
    old_state: str
    new_state: str
    current_value: float | None
    warn_threshold: float
    critical_threshold: float
    error_budget: float

    @classmethod
    def create(
        cls,
        *,
        slo_name: str,
        slo_label: str,
        old_state: str,
        new_state: str,
        current_value: float | None,
        warn_threshold: float,
        critical_threshold: float,
        error_budget: float,
    ) -> "SLOStateChangedEvent":
        return cls(
            event_id=uuid.uuid4(),
            event_version=1,
            occurred_at=datetime.now(tz=timezone.utc),
            correlation_id=uuid.uuid4(),
            slo_name=slo_name,
            slo_label=slo_label,
            old_state=old_state,
            new_state=new_state,
            current_value=current_value,
            warn_threshold=warn_threshold,
            critical_threshold=critical_threshold,
            error_budget=error_budget,
        )
