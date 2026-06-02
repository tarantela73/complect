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
        ]
