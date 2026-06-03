import uuid

from django.db import models


class OutboxArchive(models.Model):
    """
    Archive table for processed OutboxRecord rows (ADR-0013).

    Normal lifecycle class records (is_processed=True, failed_permanently_at IS NULL)
    are moved here after OUTBOX_ARCHIVE_WINDOW_DAYS. Schema mirrors OutboxRecord.
    No indexes beyond PK — this is forensic storage, not an operational table.
    """

    id = models.UUIDField(primary_key=True, editable=False)
    event_type = models.CharField(max_length=255)
    payload_json = models.JSONField()
    correlation_id = models.UUIDField()
    causal_root_id = models.UUIDField(null=True, blank=True)
    aggregate_id = models.UUIDField(null=True, blank=True)
    aggregate_type = models.CharField(max_length=255, null=True, blank=True)
    aggregate_seq = models.BigIntegerField(null=True, blank=True)
    is_processed = models.BooleanField(default=False)
    created_at = models.DateTimeField()
    processed_at = models.DateTimeField(null=True, blank=True)
    retry_count = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(null=True, blank=True)
    failed_permanently_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "common_outbox_archive"
