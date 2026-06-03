from datetime import timedelta

import structlog
from django.conf import settings
from django.utils import timezone as tz

from apps.common.models.outbox import OutboxRecord
from apps.common.services.causal_pair_registry import CAUSAL_PAIRS, CausalDivergenceClassifier
from apps.common.tasks import BaseEnterpriseTask
from config.celery import app

logger = structlog.get_logger(__name__)


@app.task(base=BaseEnterpriseTask, queue="notifications")
def check_causal_pair_slo_task() -> None:
    """
    Observability-only scan for causal pair SLO violations (ADR-0014).

    Emits structured warnings for DETECTABLE divergence (beyond SLO, within retention).
    Does NOT initiate repair — that requires explicit operator action.
    """
    now = tz.now()
    retention_cutoff = now - timedelta(days=settings.OUTBOX_ARCHIVE_WINDOW_DAYS)

    for pair_def in CAUSAL_PAIRS:
        slo_threshold = now - timedelta(seconds=pair_def.expected_within_seconds)

        source_records = list(
            OutboxRecord.objects.filter(
                event_type=pair_def.source_event,
                created_at__lt=slo_threshold,
                created_at__gte=retention_cutoff,
                is_processed=True,
                failed_permanently_at__isnull=True,
            ).values("id", "causal_root_id", "created_at")[:500]
        )

        for source in source_records:
            root_id = source["causal_root_id"] or source["id"]
            expected_exists = OutboxRecord.objects.filter(
                causal_root_id=root_id,
                event_type=pair_def.expected_event,
            ).exists()
            if not expected_exists:
                divergence_state = CausalDivergenceClassifier.classify(
                    pair_def=pair_def,
                    source_created_at=source["created_at"],
                    reference_time=now,
                )
                logger.warning(
                    "causal_pair_slo_violation",
                    pair_name=pair_def.name,
                    source_event=pair_def.source_event,
                    expected_event=pair_def.expected_event,
                    source_outbox_id=str(source["id"]),
                    causal_root_id=str(root_id),
                    divergence_state=divergence_state,
                    age_seconds=int((now - source["created_at"]).total_seconds()),
                )
