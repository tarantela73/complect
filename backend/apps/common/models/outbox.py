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

    class Meta:
        db_table = "common_transactional_outbox"

        ordering = ["created_at"]

        indexes = [
            models.Index(
                fields=["is_processed", "created_at"],
                name="outbox_dispatch_idx",
            ),
        ]

