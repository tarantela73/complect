from __future__ import annotations

from django.db import models

from apps.common.models.base import BaseEnterpriseModel
from apps.contragents.models.contragent import Contragent


class ContragentAttachment(BaseEnterpriseModel):
    """
    Junction record: один файл (по file_id) прикреплён к одному контрагенту.

    file_id — UUID без ORM FK → domain isolation (ADR-0005).
    """

    contragent = models.ForeignKey(
        Contragent,
        on_delete=models.PROTECT,
        related_name="attachments",
        db_index=True,
    )

    file_id = models.UUIDField(db_index=True)

    attached_by = models.UUIDField(editable=False)

    class Meta:
        app_label = "contragents"
        db_table = "contragents_attachments"
        constraints = [
            models.UniqueConstraint(
                fields=["contragent", "file_id"],
                condition=models.Q(is_deleted=False),
                name="unique_active_contragent_file",
            ),
        ]
        indexes = [
            models.Index(
                fields=["contragent", "is_deleted"],
                name="cga_contragent_deleted_idx",
            ),
            models.Index(
                fields=["tenant_id", "file_id"],
                name="cga_tenant_file_idx",
            ),
        ]
