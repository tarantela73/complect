"""
Ops Console — pure read-only aggregator over existing infrastructure.
No domain logic, no tenant scope. Cross-tenant by design (platform admin only).
"""
from __future__ import annotations

import structlog
from django.http import HttpRequest
from django.utils import timezone as tz
from ninja import Router, Schema

from apps.common.api.permissions import require_permission
from apps.common.slo.definitions import SLO_DEFINITIONS
from apps.common.slo.metrics import (
    collect_celery_queue_depths,
    collect_circuit_breakers,
    collect_dlq_count,
    collect_outbox,
    collect_ws_connections,
)
from apps.common.slo.state_tracker import get_error_budget, get_slo_display_state

logger = structlog.get_logger(__name__)
router = Router(tags=["ops"])


class OutboxStats(Schema):
    pending: int
    failed_permanently: int
    lag_seconds_p95: float | None = None


class SLOMetricStatus(Schema):
    name: str
    label: str
    state: str
    error_budget: float


class OpsSnapshot(Schema):
    outbox: OutboxStats
    celery: dict[str, int]
    circuit_breakers: dict[str, str]
    dlq: int
    ws_connections: int
    slo: list[SLOMetricStatus]
    collected_at: str


def _slo_statuses() -> list[SLOMetricStatus]:
    return [
        SLOMetricStatus(
            name=slo.name,
            label=slo.label,
            state=get_slo_display_state(slo.name),
            error_budget=get_error_budget(slo.name),
        )
        for slo in SLO_DEFINITIONS
    ]


@router.get("/", response={200: OpsSnapshot, 403: dict})
@require_permission("read", "ops")
def get_ops_snapshot(request: HttpRequest):
    outbox = collect_outbox()
    return 200, OpsSnapshot(
        outbox=OutboxStats(
            pending=outbox["pending"],
            failed_permanently=outbox["failed_permanently"],
            lag_seconds_p95=outbox["lag_p95"],
        ),
        celery=collect_celery_queue_depths(),
        circuit_breakers=collect_circuit_breakers(),
        dlq=collect_dlq_count(),
        ws_connections=collect_ws_connections(),
        slo=_slo_statuses(),
        collected_at=tz.now().isoformat(),
    )
