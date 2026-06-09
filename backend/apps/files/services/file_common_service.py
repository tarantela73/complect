from __future__ import annotations

import uuid

from apps.common.contracts.file_service_protocol import FileMetaDTO, FileServiceProtocol
from apps.files.models.file_record import FileRecord, FileStatus
from apps.files.services.storage_backend import get_storage_backend


class FileCommonService:
    """
    Реализация FileServiceProtocol.
    Единственная точка входа в domain apps/files для внешних доменов.
    Не экспортирует ORM-модели — только DTO.
    """

    def get_file_meta(
        self,
        file_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> FileMetaDTO | None:
        record: FileRecord | None = (
            FileRecord.objects.filter(id=file_id, tenant_id=tenant_id, is_deleted=False)
            .first()
        )
        if record is None:
            return None

        download_url: str | None = None
        if record.status == FileStatus.AVAILABLE:
            from django.conf import settings
            ttl = getattr(settings, "FILES_PRESIGNED_DOWNLOAD_TTL", 3600)
            storage = get_storage_backend()
            download_url = storage.generate_download_url(
                object_key=record.object_key,
                expires_in=ttl,
            )

        return FileMetaDTO(
            file_id=record.id,
            tenant_id=record.tenant_id,
            original_name=record.original_name,
            size_bytes=record.size_bytes,
            status=record.status,
            download_url=download_url,
        )

    def get_file_metas_batch(
        self,
        file_ids: list[uuid.UUID],
        tenant_id: uuid.UUID,
    ) -> dict[uuid.UUID, FileMetaDTO]:
        if not file_ids:
            return {}

        records = FileRecord.objects.filter(
            id__in=file_ids,
            tenant_id=tenant_id,
            is_deleted=False,
        )

        from django.conf import settings
        ttl = getattr(settings, "FILES_PRESIGNED_DOWNLOAD_TTL", 3600)
        storage = get_storage_backend()

        result: dict[uuid.UUID, FileMetaDTO] = {}
        for record in records:
            download_url: str | None = None
            if record.status == FileStatus.AVAILABLE:
                download_url = storage.generate_download_url(
                    object_key=record.object_key,
                    expires_in=ttl,
                )
            result[record.id] = FileMetaDTO(
                file_id=record.id,
                tenant_id=record.tenant_id,
                original_name=record.original_name,
                size_bytes=record.size_bytes,
                status=record.status,
                download_url=download_url,
            )

        return result


# Module-level singleton — инжектируется в AttachmentService
default_file_service: FileServiceProtocol = FileCommonService()
