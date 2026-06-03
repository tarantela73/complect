import uuid

from django.db import models


class CausalPairRepairRecord(models.Model):
    """
    Audit trail for cross-domain causal pair repair actions (ADR-0014).

    Mandatory for Finance domain flows (repair_requires_sign_off=True).
    Records the full lifecycle: initiated → completed → signed off.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pair_name = models.CharField(max_length=255)
    source_event_id = models.UUIDField(db_index=True)
    source_event_type = models.CharField(max_length=255)
    expected_event_type = models.CharField(max_length=255)
    divergence_state = models.CharField(max_length=16)
    repair_initiated_at = models.DateTimeField(auto_now_add=True)
    repair_initiated_by = models.CharField(max_length=255)
    repair_completed_at = models.DateTimeField(null=True, blank=True)
    compensating_transaction_id = models.UUIDField(null=True, blank=True)
    sign_off_by = models.CharField(max_length=255, null=True, blank=True)
    sign_off_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "common_causal_pair_repair_records"
