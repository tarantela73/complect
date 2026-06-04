from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.files.models.file_record import FileStatus
from apps.files.repositories.file_repo import FileRepository
from apps.files.services.storage_backend import StorageBackend, get_storage_backend


@dataclass(frozen=True, slots=True)
class DownloadUrlResult:
    file_id: uuid.UUID
    download_url: str
    expires_in: int
    object_key: str


class DownloadService:
    """
    [QUERY SIDE] Generates a time-limited presigned download URL.

    Business invariant: only AVAILABLE files can be downloaded.
    QUARANTINED / SCANNING / UPLOADED / PENDING_UPLOAD → ValidationError (422 at API layer).
    """

    @staticmethod
    def get_download_url(
        *,
        file_id: uuid.UUID,
        storage: StorageBackend | None = None,
        expires_in: int | None = None,
    ) -> DownloadUrlResult:
        ttl: int = expires_in if expires_in is not None else getattr(
            settings, "FILES_PRESIGNED_DOWNLOAD_TTL", 3600
        )

        record = FileRepository.get_by_id(file_id)

        if record.status != FileStatus.AVAILABLE:
            raise ValidationError(
                f"Файл {file_id} недоступен для скачивания (статус: {record.status})."
            )

        _storage = storage or get_storage_backend()
        url = _storage.generate_download_url(object_key=record.object_key, expires_in=ttl)

        return DownloadUrlResult(
            file_id=file_id,
            download_url=url,
            expires_in=ttl,
            object_key=record.object_key,
        )
