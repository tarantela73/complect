from __future__ import annotations

from django.db import models

from apps.common.models.base import BaseEnterpriseModel
from apps.contracts.models.contract import Contract


class AttachmentType(models.TextChoices):
    CONTRACT_SIGNED = "CONTRACT_SIGNED", "Подписанный договор"
    ACT_COMPLETED = "ACT_COMPLETED", "Акт выполненных работ"
    ESTIMATE = "ESTIMATE", "Смета"
    INVOICE = "INVOICE", "Счёт"
    OTHER = "OTHER", "Прочее"


class ContractAttachment(BaseEnterpriseModel):
    """
    Junction record: один файл (по file_id) прикреплён к одному договору.

    file_id — UUID без ORM FK → domain isolation (ADR-0005).
    Физическая целостность обеспечивается на уровне AttachmentService,
    который валидирует существование и статус файла через FileServiceProtocol.
    """

    contract = models.ForeignKey(
        Contract,
        on_delete=models.PROTECT,
        related_name="attachments",
        db_index=True,
    )

    # Loose reference to apps.files domain — no ORM FK intentionally
    file_id = models.UUIDField(db_index=True)

    attached_by = models.UUIDField(editable=False)

    attachment_type = models.CharField(
        max_length=20,
        choices=AttachmentType.choices,
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "contracts_attachments"
        constraints = [
            # Один файл прикрепляется к одному договору только один раз (среди не удалённых)
            models.UniqueConstraint(
                fields=["contract", "file_id"],
                condition=models.Q(is_deleted=False),
                name="unique_active_contract_file",
            ),
        ]
        indexes = [
            models.Index(
                fields=["contract", "is_deleted"],
                name="ca_contract_deleted_idx",
            ),
            models.Index(
                fields=["tenant_id", "file_id"],
                name="ca_tenant_file_idx",
            ),
        ]
