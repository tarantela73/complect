from __future__ import annotations

from django.db import models

from apps.common.models.base import BaseEnterpriseModel

from .file_record import FileRecord


class FileAttachment(BaseEnterpriseModel):
    """
    Binds a FileRecord to an arbitrary domain aggregate (contract, supply object, etc.).

    aggregate_type / aggregate_id are logical references — no FK to domain tables,
    consistent with ADR-0005 domain isolation. Detaching a file is a soft-delete
    (is_deleted=True) so audit history is preserved.
    """

    file = models.ForeignKey(
        FileRecord,
        on_delete=models.PROTECT,
        related_name="attachments",
        db_index=True,
    )

    aggregate_type = models.CharField(
        max_length=128,
        db_index=True,
        help_text="Dotted domain identifier, e.g. 'contracts.contract'",
    )

    aggregate_id = models.UUIDField(db_index=True)

    attached_by = models.UUIDField(editable=False)

    class Meta:
        db_table = "files_file_attachments"
        indexes = [
            models.Index(
                fields=["tenant_id", "aggregate_type", "aggregate_id"],
                name="files_att_aggregate_idx",
            ),
            models.Index(
                fields=["tenant_id", "file"],
                name="files_att_tenant_file_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "file", "aggregate_type", "aggregate_id"],
                condition=models.Q(is_deleted=False),
                name="files_att_unique_active_binding",
            ),
        ]
