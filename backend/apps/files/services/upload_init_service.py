from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.common.tenancy.context_core import get_tenant_id
from apps.files.models.file_record import StorageProvider
from apps.files.repositories.file_repo import FileRepository
from apps.files.schemas.file_dto import UploadInitRequest

_MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB hard cap


@dataclass(frozen=True, slots=True)
class UploadInitResult:
    file_id: uuid.UUID
    object_key: str
    upload_url: str
    status: str


class UploadInitService:
    """
    [COMMAND SIDE] Initialises an upload slot.

    Creates FileRecord(PENDING_UPLOAD) inside a transaction and returns a
    presigned upload URL. The URL is a stub until the S3 adapter is wired;
    callers must treat upload_url as opaque and follow it verbatim.
    """

    @staticmethod
    def initialize_upload(
        *,
        actor_id: uuid.UUID,
        request: UploadInitRequest,
    ) -> UploadInitResult:
        if request.size_bytes > _MAX_FILE_SIZE:
            raise ValidationError(
                f"Размер файла {request.size_bytes} байт превышает лимит {_MAX_FILE_SIZE} байт."
            )

        tenant_id = get_tenant_id()
        file_id = uuid.uuid4()

        # object_key is deterministic: <tenant>/<file_id>/<sanitised_name>
        safe_name = _sanitise_filename(request.original_name)
        object_key = f"{tenant_id}/{file_id}/{safe_name}"

        with transaction.atomic():
            FileRepository.create_pending(
                file_id=file_id,
                object_key=object_key,
                original_name=request.original_name,
                content_type=request.content_type,
                size_bytes=request.size_bytes,
                uploaded_by=actor_id,
                storage_provider=StorageProvider.S3,
            )

        # Stub URL: replaced by S3PresignedUrlAdapter in the next iteration.
        upload_url = f"/stub/upload/{object_key}"

        return UploadInitResult(
            file_id=file_id,
            object_key=object_key,
            upload_url=upload_url,
            status="PENDING_UPLOAD",
        )


def _sanitise_filename(name: str) -> str:
    """Strip path separators and collapse whitespace to underscores."""
    import re

    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"[^\w.\-]", "_", base)[:255] or "file"
