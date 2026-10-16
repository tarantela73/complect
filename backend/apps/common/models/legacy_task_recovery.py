from __future__ import annotations

import uuid

from django.db import models


class LegacyTaskRecoveryStatus(models.TextChoices):
    UNRESOLVED = "UNRESOLVED", "Unresolved"
    READY_TO_REPLAY = "READY_TO_REPLAY", "Ready to replay"
    PROCESSING = "PROCESSING", "Processing"
    FAILED = "FAILED", "Failed"
    COMPLETED = "COMPLETED", "Completed"


class LegacyTaskRecovery(models.Model):
    """Durable evidence for one historical task delivery."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_id = models.CharField(max_length=255, unique=True)
    doc_id = models.CharField(max_length=255)
    task_name = models.CharField(max_length=255)
    args_payload = models.JSONField(default=list)
    kwargs_payload = models.JSONField(default=dict)
    metadata_payload = models.JSONField(default=dict)
    retries = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=LegacyTaskRecoveryStatus.choices,
        default=LegacyTaskRecoveryStatus.UNRESOLVED,
        db_index=True,
    )
    tenant_id = models.UUIDField(null=True, blank=True, db_index=True)
    assigned_by_actor_id = models.UUIDField(null=True, blank=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)
    capability_sha256 = models.CharField(max_length=64, blank=True)
    scan_proof = models.JSONField(default=dict)
    completed_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "common_legacy_task_recoveries"
        indexes = [
            models.Index(fields=["status", "created_at"], name="legacy_recovery_status_idx"),
            models.Index(fields=["tenant_id", "status"], name="legacy_recovery_tenant_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(retries__gte=0), name="legacy_recovery_retries_nonnegative"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="legacy_recovery_version_positive"),
        ]
