from __future__ import annotations

import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError

from apps.common.contracts.file_service_protocol import FileMetaDTO, FileServiceProtocol
from apps.contracts.repositories.attachment_repo import AttachmentRepository
from apps.contracts.repositories.contract_repo import ContractRepository

_ATTACHABLE_STATUSES = {"AVAILABLE"}


class AttachmentService:
    """
    Command side: attach / detach файлов к договору.
    FileServiceProtocol инжектируется — домен files не импортируется напрямую.
    """

    def __init__(self, file_service: FileServiceProtocol) -> None:
        self._files = file_service

    def attach(
        self,
        *,
        contract_id: uuid.UUID,
        file_id: uuid.UUID,
        actor_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> None:
        """
        Прикрепляет файл к договору.

        Инварианты:
        - Договор существует и не удалён (tenant-scoped)
        - Файл существует, принадлежит тому же тенанту, status == AVAILABLE
        - Файл ещё не прикреплён к этому договору (UniqueConstraint)
        """
        ContractRepository.get_by_id(contract_id)

        meta: FileMetaDTO | None = self._files.get_file_meta(file_id, tenant_id)
        if meta is None:
            raise PermissionDenied("FILE_NOT_FOUND")

        if meta.status not in _ATTACHABLE_STATUSES:
            raise ValidationError(
                f"Файл недоступен для прикрепления (статус: {meta.status}). "
                "Прикреплять можно только AVAILABLE файлы."
            )

        try:
            AttachmentRepository.create(
                contract_id=contract_id,
                file_id=file_id,
                attached_by=actor_id,
            )
        except IntegrityError as exc:
            raise ValidationError("Файл уже прикреплён к этому договору.") from exc

    def detach(
        self,
        *,
        contract_id: uuid.UUID,
        file_id: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> None:
        """
        Soft-detach: устанавливает is_deleted=True на записи ContractAttachment.
        Legal Hold блокирует удаление.
        """
        attachment = AttachmentRepository.get_active(contract_id, file_id)
        if attachment is None:
            raise PermissionDenied("ATTACHMENT_NOT_FOUND")

        ok = AttachmentRepository.soft_delete(attachment, deleted_by=actor_id)
        if not ok:
            raise PermissionDenied("ATTACHMENT_LEGAL_HOLD")
