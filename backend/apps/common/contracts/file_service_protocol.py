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

    def get_file_meta_for_attachment(
        self, file_id: uuid.UUID, tenant_id: uuid.UUID,
    ) -> FileMetaDTO | None:
        """Lock a live tenant file on default in the caller's transaction.

        Acquire the caller's aggregate lock first. Return metadata without
        storage I/O or a download URL; hold the file lock until commit.
        """
        ...

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

    def get_file_metas_batch(
        self,
        file_ids: list[uuid.UUID],
        tenant_id: uuid.UUID,
    ) -> dict[uuid.UUID, FileMetaDTO]:
        """
        Batch-вариант get_file_meta: один SELECT для всех file_ids, один storage backend.
        Отсутствующие / чужие / удалённые файлы отсутствуют в возвращаемом словаре.
        """
        ...


_file_service: FileServiceProtocol | None = None


def register_file_service(service: FileServiceProtocol) -> None:
    """Register the Files-owned reader at the composition root."""
    global _file_service
    if _file_service is not None and _file_service is not service:
        raise RuntimeError("File service already registered")
    _file_service = service


def get_file_service() -> FileServiceProtocol:
    if _file_service is None:
        raise RuntimeError("FILE_SERVICE_UNAVAILABLE")
    return _file_service
