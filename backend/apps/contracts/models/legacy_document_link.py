from __future__ import annotations

from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class LegacyDocumentMappingState(models.TextChoices):
    UNRESOLVED = "UNRESOLVED", "Не разрешён"
    RESOLVED = "RESOLVED", "Разрешён"
    CONFLICT = "CONFLICT", "Конфликт"


class LegacyDocumentLink(BaseEnterpriseModel):
    """Tenant-scoped, provenance-bearing reference to a Files object.

    ``target_file_id`` is deliberately a UUID reference without a Django FK:
    Contracts must not import or own the Files aggregate.
    """

    source_system = models.CharField(max_length=64)
    actor_id = models.UUIDField(editable=False)
    legacy_document_id = models.UUIDField()
    target_file_id = models.UUIDField(null=True, blank=True)
    target_version = models.PositiveIntegerField(default=1)
    legacy_revision = models.CharField(max_length=255)
    source_snapshot_sha256 = models.CharField(max_length=64)
    payload_sha256 = models.CharField(max_length=64)
    checksum_sha256 = models.CharField(max_length=64)
    storage_namespace = models.CharField(max_length=128)
    object_key = models.TextField()
    size_bytes = models.BigIntegerField()
    mapping_state = models.CharField(
        max_length=16,
        choices=LegacyDocumentMappingState.choices,
        default=LegacyDocumentMappingState.UNRESOLVED,
        db_index=True,
    )

    class Meta:
        db_table = "contracts_legacy_document_links"
        constraints = [
            # Kept global to preserve the legacy identity contract. The
            # tenant predicate and FORCE RLS still protect row contents.
            models.UniqueConstraint(
                fields=["source_system", "legacy_document_id"],
                name="contracts_legacy_link_source_doc_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(size_bytes__gte=0),
                name="contracts_legacy_link_size_nonnegative",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(mapping_state=LegacyDocumentMappingState.RESOLVED)
                    | (
                        models.Q(target_file_id__isnull=False)
                        & ~models.Q(source_system="")
                        & ~models.Q(legacy_revision="")
                        & ~models.Q(source_snapshot_sha256="")
                        & ~models.Q(payload_sha256="")
                        & ~models.Q(checksum_sha256="")
                        & ~models.Q(storage_namespace="")
                        & ~models.Q(object_key="")
                    )
                ),
                name="contracts_legacy_link_resolved_complete",
            ),
            models.CheckConstraint(
                condition=models.Q(source_system="COMMON_DOCUMENT"),
                name="contracts_legacy_link_source_system",
            ),
            models.CheckConstraint(
                condition=models.Q(source_snapshot_sha256__regex=r"^[0-9a-f]{64}$")
                & models.Q(payload_sha256__regex=r"^[0-9a-f]{64}$")
                & models.Q(checksum_sha256__regex=r"^[0-9a-f]{64}$"),
                name="contracts_legacy_link_sha256_lower_hex",
            ),
        ]
