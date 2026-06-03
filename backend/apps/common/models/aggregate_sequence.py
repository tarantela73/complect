import uuid

from django.db import models


class AggregateSequence(models.Model):
    """
    Per-aggregate monotonic sequence allocator (ADR-0012, Mode A + Mode B).

    Row serves as a counter: allocation increments current_seq atomically via
    INSERT ... ON CONFLICT DO UPDATE RETURNING — no MAX() scan, no table lock.
    Rollback of the outer business transaction rolls back the increment:
    no gap can appear in the committed sequence for a tenant's aggregate.

    Manipulate ONLY via AggregateSequenceAllocator.allocate().
    """

    tenant_id = models.UUIDField()
    aggregate_type = models.CharField(max_length=255)
    aggregate_id = models.UUIDField()
    current_seq = models.BigIntegerField(default=0)

    class Meta:
        db_table = "common_aggregate_sequences"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "aggregate_type", "aggregate_id"],
                name="aggregate_sequence_natural_pk",
            )
        ]


class AggregateSequenceFence(models.Model):
    """
    Per-(aggregate, consumer) monotonic fence for Mode B consumers (ADR-0012).

    Guards against applying stale snapshot events: consumer advances the fence
    atomically and discards events whose seq <= last_seen_seq.

    NOT used for Mode A (delta) events — Mode A guarantees in-order delivery
    at the dispatcher layer; the fence is a consumer-side guard for snapshots.

    Manipulate via AggregateSequenceFenceService.advance().
    """

    tenant_id = models.UUIDField()
    aggregate_type = models.CharField(max_length=255)
    aggregate_id = models.UUIDField()
    consumer = models.CharField(max_length=255)
    last_seen_seq = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "common_aggregate_sequence_fences"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "aggregate_type", "aggregate_id", "consumer"],
                name="aggregate_sequence_fence_natural_pk",
            )
        ]
