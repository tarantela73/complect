"""
CQRS Query Side — read-only projections for contract attachments.
Cross-domain read goes through FileServiceProtocol, no ORM FK to files.
"""
from __future__ import annotations

import uuid

from apps.common.contracts.file_service_protocol import FileMetaDTO, FileServiceProtocol
from apps.contracts.repositories.attachment_repo import AttachmentRepository
from apps.contracts.schemas.attachment_dto import AttachmentListResponse, AttachmentResponse


def get_attachments_list(
    contract_id: uuid.UUID,
    tenant_id: uuid.UUID,
    file_service: FileServiceProtocol,
) -> AttachmentListResponse:
    file_ids = AttachmentRepository.list_file_ids(contract_id)

    results: list[AttachmentResponse] = []
    for file_id in file_ids:
        meta: FileMetaDTO | None = file_service.get_file_meta(file_id, tenant_id)
        if meta is None:
            continue  # Файл удалён из Files domain — тихо пропускаем

        # attached_by / attached_at берём из AttachmentRepository
        attachment = AttachmentRepository.get_active(contract_id, file_id)
        if attachment is None:
            continue

        results.append(
            AttachmentResponse(
                file_id=meta.file_id,
                name=meta.original_name,
                size_bytes=meta.size_bytes,
                status=meta.status,
                download_url=meta.download_url,
                attached_by=attachment.attached_by,
                attached_at=attachment.created_at,
            )
        )

    return AttachmentListResponse(results=results, count=len(results))
