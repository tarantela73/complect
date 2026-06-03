import uuid

from django.db import models


class OutboxRecord(models.Model):
    """
    Transactional Outbox Pattern.
    Гарантированная доставка доменных событий.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    event_type = models.CharField(
        max_length=255,
        db_index=True,
    )

    payload_json = models.JSONField()

    correlation_id = models.UUIDField(
        db_index=True,
    )

    # Logical causal root of this event chain (ADR-0015).
    # Normal flow: causal_root_id == self.id (set by push_to_outbox).
    # Repair/compensation flow: causal_root_id == original OutboxRecord.id
    # that initiated the causal chain being repaired.
    # Nullable for backward compat — existing records pre-ADR-0015 treated as root.
    causal_root_id = models.UUIDField(
        null=True,
        blank=True,
    )

    # Aggregate causal integrity fields (ADR-0012).
    # aggregate_seq = None means the event's causal position in the aggregate history
    # is unknown — a data quality gap, not an optional feature.
    # Set by AggregateSequenceAllocator.allocate() inside the business transaction.
    aggregate_id = models.UUIDField(null=True, blank=True)
    aggregate_type = models.CharField(max_length=255, null=True, blank=True)
    aggregate_seq = models.BigIntegerField(null=True, blank=True)

    is_processed = models.BooleanField(
        default=False,
        db_index=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    processed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    retry_count = models.PositiveSmallIntegerField(default=0)

    last_error = models.TextField(null=True, blank=True)

    failed_permanently_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    next_retry_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "common_transactional_outbox"

        ordering = ["created_at"]

        indexes = [
            models.Index(
                fields=["is_processed", "created_at"],
                name="outbox_dispatch_idx",
            ),
            models.Index(
                fields=["next_retry_at", "created_at"],
                name="outbox_ready_dispatch_idx",
                condition=models.Q(is_processed=False, failed_permanently_at__isnull=True),
            ),
            models.Index(
                fields=["causal_root_id"],
                name="outbox_causal_root_idx",
                condition=models.Q(causal_root_id__isnull=False),
            ),
            models.Index(
                fields=["aggregate_type", "aggregate_id", "aggregate_seq"],
                name="outbox_aggregate_seq_idx",
                condition=models.Q(aggregate_seq__isnull=False),
            ),
        ]

