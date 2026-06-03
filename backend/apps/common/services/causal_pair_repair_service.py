import uuid

import structlog
from django.utils import timezone as tz

from apps.common.models.causal_pair_repair import CausalPairRepairRecord
from apps.common.services.causal_pair_registry import DivergenceState

logger = structlog.get_logger(__name__)


class CausalPairRepairService:
    """
    Audit trail service for cross-domain causal pair repair actions (ADR-0014).

    Finance flows require human sign-off: call initiate_repair() before dispatching
    ConsumerRepairService.reset_for_repair(), then complete_repair() after sign-off.
    """

    @staticmethod
    def initiate_repair(
        *,
        pair_name: str,
        source_event_id: uuid.UUID,
        source_event_type: str,
        expected_event_type: str,
        divergence_state: DivergenceState,
        initiated_by: str,
        notes: str | None = None,
    ) -> CausalPairRepairRecord:
        record = CausalPairRepairRecord.objects.create(
            pair_name=pair_name,
            source_event_id=source_event_id,
            source_event_type=source_event_type,
            expected_event_type=expected_event_type,
            divergence_state=divergence_state,
            repair_initiated_by=initiated_by,
            notes=notes,
        )
        logger.warning(
            "causal_pair_repair_initiated",
            repair_record_id=str(record.id),
            pair_name=pair_name,
            source_event_id=str(source_event_id),
            divergence_state=divergence_state,
            initiated_by=initiated_by,
        )
        return record

    @staticmethod
    def complete_repair(
        *,
        repair_record_id: uuid.UUID,
        compensating_transaction_id: uuid.UUID,
        sign_off_by: str,
    ) -> None:
        now = tz.now()
        updated = CausalPairRepairRecord.objects.filter(
            id=repair_record_id,
            repair_completed_at__isnull=True,
        ).update(
            repair_completed_at=now,
            compensating_transaction_id=compensating_transaction_id,
            sign_off_by=sign_off_by,
            sign_off_at=now,
        )
        if not updated:
            raise ValueError(
                f"CausalPairRepairRecord {repair_record_id} not found or already completed"
            )
        logger.info(
            "causal_pair_repair_completed",
            repair_record_id=str(repair_record_id),
            compensating_transaction_id=str(compensating_transaction_id),
            sign_off_by=sign_off_by,
        )
