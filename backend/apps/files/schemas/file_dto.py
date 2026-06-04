from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class UploadInitRequest(BaseModel):
    original_name: str = Field(..., max_length=512)
    content_type: str = Field(..., max_length=128)
    size_bytes: int = Field(..., gt=0, description="Размер файла в байтах")


class UploadInitResponse(BaseModel):
    file_id: uuid.UUID
    object_key: str
    upload_url: str
    status: str


class UploadCompleteRequest(BaseModel):
    checksum_sha256: str = Field(..., min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class DownloadUrlResponse(BaseModel):
    file_id: uuid.UUID
    download_url: str
    expires_in: int
    object_key: str


class FileDetailResponse(BaseModel):
    id: uuid.UUID
    object_key: str
    original_name: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    storage_provider: str
    status: str
    uploaded_by: uuid.UUID
    scanned_at: Optional[datetime] = None
    quarantine_reason: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FileListResponse(BaseModel):
    results: list[FileDetailResponse]
    next_cursor: Optional[str] = None
    count: int
