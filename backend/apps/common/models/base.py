import uuid

from django.db import models
from django.db.models import Q


class BaseEnterpriseModel(models.Model):
    """
    Стерильный базовый класс.

    ВАЖНО:
    - запрещены save/delete/clean
    - только декларативная схема БД
    - tenant isolation обязателен
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    tenant_id = models.UUIDField(
        db_index=True,
        editable=False,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
    )

    is_deleted = models.BooleanField(
        default=False,
        db_index=True,
    )

    version = models.PositiveIntegerField(
        default=1,
        editable=False,
    )

    is_legal_hold = models.BooleanField(
        default=False,
        db_index=True,
    )

    class Meta:
        abstract = True
        indexes = [
            models.Index(
                fields=["tenant_id", "created_at"],
                name="idx_tenant_created",
            ),
            models.Index(
                fields=["tenant_id", "is_deleted"],
                name="idx_tenant_deleted",
            ),
            models.Index(
                fields=["tenant_id", "id"],
                name="idx_tenant_id",
            ),
            models.Index(
                fields=["tenant_id", "created_at", "id"],
                name="idx_tenant_cursor",
            ),
        ]
        
        
class AuditIntegrityLog(models.Model):
    """
    Append-only audit chain.

    ВАЖНО:
    - никаких update/delete
    - immutable event ledger
    - используется только insert
    """

    id = models.BigAutoField(
        primary_key=True,
        editable=False,
    )

    tenant_id = models.UUIDField(
        db_index=True,
        editable=False,
    )

    timestamp = models.DateTimeField(
        db_index=True,
        editable=False,
    )

    action_type = models.CharField(
        max_length=100,
        editable=False,
    )

    user_id = models.UUIDField(
        editable=False,
    )

    request_id = models.UUIDField(
        editable=False,
    )

    correlation_id = models.UUIDField(
        db_index=True,
        editable=False,
    )

    payload_json = models.JSONField(
        editable=False,
    )

    previous_hash = models.CharField(
        max_length=64,
        editable=False,
    )

    current_hash = models.CharField(
        max_length=64,
        editable=False,
        unique=True,
    )

    class Meta:
        db_table = "common_audit_integrity_logs"
        ordering = ["-id"]
        indexes = [
            models.Index(
                fields=["tenant_id", "timestamp"],
                name="audit_tenant_ts_idx",
            ),
            models.Index(
                fields=["correlation_id"],
                name="audit_corr_idx",
            ),
            models.Index(
                fields=["action_type", "timestamp"],
                name="audit_action_ts_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(current_hash=""),
                name="audit_current_hash_not_empty",
            ),
        ]        