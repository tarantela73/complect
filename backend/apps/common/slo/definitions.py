from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class SLODefinition:
    name: str
    label: str
    warn: float
    critical: float
    window_seconds: int = 1800


SLO_DEFINITIONS: tuple[SLODefinition, ...] = (
    SLODefinition("outbox.lag_p95", "Outbox Lag p95 (s)", warn=30, critical=300),
    SLODefinition("outbox.pending", "Outbox Pending", warn=10, critical=100),
    SLODefinition("outbox.failed_permanently", "Outbox Failed Permanently", warn=1, critical=5),
    SLODefinition("celery.outbox", "Celery Outbox Queue", warn=50, critical=500),
    SLODefinition("dlq", "DLQ Depth", warn=1, critical=10),
)
