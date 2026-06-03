import uuid

from django.db import models


class ProcessedEvent(models.Model):
    """
    Consumer-side deduplication gate for at-least-once delivery.

    The unique constraint on (outbox_record_id, consumer) is the primary
    idempotency guarantee — not application-level checks.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    outbox_record_id = models.UUIDField()
    consumer = models.CharField(max_length=255)
    tenant_id = models.UUIDField()
    processed_at = models.DateTimeField(auto_now_add=True)
    # NULL = claimed but not yet acked (in-flight or crashed consumer).
    # NOT NULL = processing confirmed complete.
    acked_at = models.DateTimeField(null=True, blank=True)
    # Worker/pod UUID that claimed this event. ack() verifies ownership when provided,
    # preventing cross-instance ACK (e.g., instance B acking what instance A claimed).
    claimed_by_instance_id = models.UUIDField(null=True, blank=True)
    # Lease expiry for crash recovery (ADR-0011). NULL = no lease (legacy, infinite).
    # When set, reclaim() can atomically take over after this timestamp.
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    # Logical causal root of the event chain this consumer is processing (ADR-0015/0016).
    # Propagated from OutboxRecord.causal_root_id via the Redis Stream payload.
    # Enables Class A causal queries without joining back to OutboxRecord (post-archival).
    # Nullable for backward compat — pre-ADR-0015 claims treated as self-rooted.
    causal_root_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "common_processed_events"

        constraints = [
            models.UniqueConstraint(
                fields=["outbox_record_id", "consumer"],
                name="processed_event_consumer_unique",
            ),
        ]

        indexes = [
            models.Index(
                fields=["consumer", "tenant_id", "processed_at"],
                name="processed_event_lookup_idx",
            ),
            # Covers replay detection: find in-flight / crashed consumers efficiently.
            models.Index(
                fields=["consumer", "tenant_id", "processed_at"],
                name="processed_event_unacked_idx",
                condition=models.Q(acked_at__isnull=True),
            ),
            # Covers reclaim() scan: expired leases on unacked records.
            models.Index(
                fields=["consumer", "lease_expires_at"],
                name="processed_event_lease_idx",
                condition=models.Q(acked_at__isnull=True, lease_expires_at__isnull=False),
            ),
            # Covers CausalQueryService.get_chain_by_root() post-archival traversal (ADR-0016).
            models.Index(
                fields=["causal_root_id"],
                name="pe_causal_root_idx",
                condition=models.Q(causal_root_id__isnull=False),
            ),
        ]
