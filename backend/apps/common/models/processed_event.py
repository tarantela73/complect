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
        ]
