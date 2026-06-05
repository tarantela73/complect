from __future__ import annotations

import structlog
from django.db import transaction

from apps.common.events.slo import SLOStateChangedEvent
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.slo.definitions import SLO_DEFINITIONS
from apps.common.slo.evaluator import evaluate, extract_metric
from apps.common.slo.metrics import collect_snapshot
from apps.common.slo.state_tracker import get_error_budget, record_evaluation
from apps.common.tasks import BaseEnterpriseTask
from config.celery import app

logger = structlog.get_logger(__name__)


@app.task(
    base=BaseEnterpriseTask,
    name="apps.common.tasks.slo_compliance.check_slo_compliance_task",
    queue="notifications",
    ignore_result=True,
)
def check_slo_compliance_task() -> None:
    try:
        snapshot = collect_snapshot()
    except Exception as exc:
        logger.error("slo_snapshot_failed", error=str(exc))
        return

    for slo in SLO_DEFINITIONS:
        value = extract_metric(snapshot, slo.name)
        raw_state = evaluate(value, slo)
        old_display, new_display = record_evaluation(slo.name, raw_state)

        if old_display == new_display:
            continue

        # Suppress ok→recovering: it's a cosmetic UI transition, not a real event.
        if old_display == "ok" and new_display == "recovering":
            continue

        budget = get_error_budget(slo.name)
        event = SLOStateChangedEvent.create(
            slo_name=slo.name,
            slo_label=slo.label,
            old_state=old_display,
            new_state=new_display,
            current_value=value,
            warn_threshold=float(slo.warn),
            critical_threshold=float(slo.critical),
            error_budget=budget,
        )

        try:
            with transaction.atomic():
                TransactionalOutboxService.push_to_outbox(event)
        except Exception as exc:
            logger.error("slo_outbox_write_failed", slo_name=slo.name, error=str(exc))

        logger.warning(
            "slo_state_transition",
            slo_name=slo.name,
            old_state=old_display,
            new_state=new_display,
            value=value,
            budget=budget,
        )
