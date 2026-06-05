from __future__ import annotations

from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class StorageProvider(models.TextChoices):
    S3 = "S3", "Amazon S3"
    LOCAL = "LOCAL", "Local (dev only)"


class FileStatus(models.TextChoices):
    PENDING_UPLOAD = "PENDING_UPLOAD", "Ожидает загрузки"
    UPLOADED = "UPLOADED", "Загружен"
    SCANNING = "SCANNING", "Сканируется"
    AVAILABLE = "AVAILABLE", "Доступен"
    QUARANTINED = "QUARANTINED", "На карантине"
    DELETED = "DELETED", "Удалён"


class FileRecord(BaseEnterpriseModel):
    """
    Metadata record for an uploaded file. The physical file lives in S3;
    this table is the authoritative source for lifecycle state and attachments.

    original_name stores the user-visible filename and may contain PII
    (e.g. "Паспорт_Иванов_И.И.pdf"). Encryption via pgcrypto Vault is
    applied at the repository layer — the column is plain VARCHAR here
    to defer migration risk to a dedicated Expand & Contract cycle.
    """

    object_key = models.CharField(
        max_length=1024,
        help_text="S3 object key: <tenant_id>/<file_id>/<sanitised_name>",
    )

    original_name = models.CharField(max_length=512)

    content_type = models.CharField(max_length=128)

    size_bytes = models.BigIntegerField()

    checksum_sha256 = models.CharField(max_length=64)

    storage_provider = models.CharField(
        max_length=16,
        choices=StorageProvider.choices,
        default=StorageProvider.S3,
    )

    status = models.CharField(
        max_length=20,
        choices=FileStatus.choices,
        default=FileStatus.PENDING_UPLOAD,
        db_index=True,
    )

    uploaded_by = models.UUIDField(editable=False)

    scanned_at = models.DateTimeField(null=True, blank=True)

    quarantine_reason = models.CharField(max_length=512, blank=True, default="")

    search_vector = models.GeneratedField(
        expression=SearchVector("original_name", config="pg_catalog.russian", weight="B"),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        db_table = "files_file_records"
        indexes = [
            models.Index(fields=["tenant_id", "status"], name="files_rec_tenant_status_idx"),
            models.Index(fields=["tenant_id", "uploaded_by"], name="files_rec_tenant_uploader_idx"),
            models.Index(fields=["checksum_sha256"], name="files_rec_checksum_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(size_bytes__gte=0),
                name="files_rec_size_non_negative",
            ),
            models.CheckConstraint(
                condition=~models.Q(object_key=""),
                name="files_rec_object_key_not_empty",
            ),
        ]
