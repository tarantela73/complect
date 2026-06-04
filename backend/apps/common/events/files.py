import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import ClassVar


@dataclass(slots=True, frozen=True)
class FileUploadedEvent:
    EVENT_TYPE: ClassVar[str] = "files.FileUploaded"

    event_id: uuid.UUID
    event_version: int
    occurred_at: datetime
    correlation_id: uuid.UUID
    file_id: uuid.UUID
    tenant_id: uuid.UUID
    uploaded_by: uuid.UUID
    object_key: str
    original_name: str
    content_type: str
    size_bytes: int
    checksum_sha256: str

    @classmethod
    def create(
        cls,
        *,
        correlation_id: uuid.UUID,
        file_id: uuid.UUID,
        tenant_id: uuid.UUID,
        uploaded_by: uuid.UUID,
        object_key: str,
        original_name: str,
        content_type: str,
        size_bytes: int,
        checksum_sha256: str,
    ) -> "FileUploadedEvent":
        return cls(
            event_id=uuid.uuid4(),
            event_version=1,
            occurred_at=datetime.now(tz=timezone.utc),
            correlation_id=correlation_id,
            file_id=file_id,
            tenant_id=tenant_id,
            uploaded_by=uploaded_by,
            object_key=object_key,
            original_name=original_name,
            content_type=content_type,
            size_bytes=size_bytes,
            checksum_sha256=checksum_sha256,
        )
