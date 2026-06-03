# backend/apps/common/tasks/chain_completeness_monitor.py
import uuid
from collections import defaultdict
from datetime import timedelta

import structlog
from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone as tz

from apps.common.models.outbox import OutboxRecord
from apps.common.models.processed_event import ProcessedEvent

logger = structlog.get_logger(__name__)


class ChainCompletenessMonitor:
    """
    Bulk-measures causal chain completeness against the SLO window (ADR-0017).

    A chain is complete when every non-DLQ'd OutboxRecord node has a corresponding
    ProcessedEvent with acked_at IS NOT NULL. DLQ'd nodes (failed_permanently_at IS NOT NULL)
    are excluded — they are a separate failure mode tracked by DLQ rate SLO.

    Algorithm: exactly 3 DB queries regardless of chain count (Invariant 2 of ADR-0017).

    Chain key derivation: COALESCE(causal_root_id, id).
    Pre-ADR-0015 records have causal_root_id=NULL → each is treated as its own self-rooted
    single-node chain, identified by its own id. This avoids IN(NULL) pitfalls in Query 2.
    """

    @staticmethod
    def measure() -> dict:
        slo_seconds: int = getattr(settings, "CHAIN_COMPLETENESS_SLO_WINDOW_SECONDS", 300)
        slack_seconds: int = getattr(settings, "CHAIN_COMPLETENESS_OBSERVATION_SLACK_SECONDS", 300)
        slo_target: float = getattr(settings, "CHAIN_COMPLETENESS_SLO_TARGET", 0.999)

        now = tz.now()
        # Observation window: records that have passed the SLO deadline (Invariant 1).
        window_end = now - timedelta(seconds=slo_seconds)
        window_start = window_end - timedelta(seconds=slack_seconds)

        # Query 1: distinct chain keys (COALESCE(causal_root_id, id)) in the observation window.
        # Using record.id as fallback for pre-ADR-0015 null-root records avoids IN(NULL) in Q2.
        root_ids_in_window: set[uuid.UUID] = set(
            OutboxRecord.objects.filter(
                created_at__gte=window_start,
                created_at__lt=window_end,
                failed_permanently_at__isnull=True,
            )
            .annotate(chain_key=Coalesce("causal_root_id", "id"))
            .values_list("chain_key", flat=True)
            .distinct()
        )

        if not root_ids_in_window:
            return {
                "total_chains": 0,
                "complete_chains": 0,
                "incomplete_chains": 0,
                "completeness_rate": None,
                "slo_violated": False,
            }

        # Query 2: all OutboxRecord nodes belonging to these chains
        # (includes compensation records created outside the window — Invariant 4).
        # Q(id__in) covers null-root self-rooted records (their chain_key == their own id).
        chain_records = list(
            OutboxRecord.objects.filter(
                Q(causal_root_id__in=root_ids_in_window) | Q(id__in=root_ids_in_window),
                failed_permanently_at__isnull=True,
            ).values("id", "causal_root_id")
        )

        all_chain_ids: set[uuid.UUID] = {r["id"] for r in chain_records}

        # Query 3: acked ProcessedEvents for all chain nodes.
        acked_ids: set[uuid.UUID] = set(
            ProcessedEvent.objects.filter(
                outbox_record_id__in=all_chain_ids,
                acked_at__isnull=False,
            ).values_list("outbox_record_id", flat=True)
        )

        root_to_record_ids: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        for r in chain_records:
            # Pre-ADR-0015 records have causal_root_id=NULL → treat as self-rooted.
            root_id: uuid.UUID = r["causal_root_id"] or r["id"]
            root_to_record_ids[root_id].add(r["id"])

        complete_chains = sum(
            1
            for record_ids in root_to_record_ids.values()
            if record_ids.issubset(acked_ids)
        )
        total_chains = len(root_to_record_ids)
        incomplete_chains = total_chains - complete_chains
        completeness_rate = complete_chains / total_chains

        return {
            "total_chains": total_chains,
            "complete_chains": complete_chains,
            "incomplete_chains": incomplete_chains,
            "completeness_rate": completeness_rate,
            "slo_violated": completeness_rate < slo_target,
        }


@shared_task(
    name="apps.common.tasks.chain_completeness_monitor.run_chain_completeness_monitor_task",
    queue="outbox",
    ignore_result=True,
)
def run_chain_completeness_monitor_task() -> None:
    metrics = ChainCompletenessMonitor.measure()

    log_fn = logger.warning if metrics["slo_violated"] else logger.info
    log_fn(
        "chain_completeness_measured",
        total_chains=metrics["total_chains"],
        complete_chains=metrics["complete_chains"],
        incomplete_chains=metrics["incomplete_chains"],
        completeness_rate=metrics["completeness_rate"],
        slo_violated=metrics["slo_violated"],
    )

    if metrics["slo_violated"]:
        logger.error(
            "chain_completeness_slo_violated",
            completeness_rate=metrics["completeness_rate"],
            incomplete_chains=metrics["incomplete_chains"],
        )
