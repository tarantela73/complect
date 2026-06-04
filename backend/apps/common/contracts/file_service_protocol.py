from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class FileMetaDTO:
    file_id: uuid.UUID
    tenant_id: uuid.UUID
    original_name: str
    size_bytes: int
    status: str
    download_url: str | None  # None when status != AVAILABLE


@runtime_checkable
class FileServiceProtocol(Protocol):
    """
    [ADR-0005] Абстрактный контракт для чтения метаданных файлов из домена contracts.
    Реализацию предоставляет apps.files — она инжектируется в AttachmentService.
    """

    def get_file_meta(
        self,
        file_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> FileMetaDTO | None:
        """
        Возвращает метаданные файла или None, если файл не существует / другой тенант.
        Включает presigned download_url (TTL 3600s) только для AVAILABLE файлов.
        """
        ...
