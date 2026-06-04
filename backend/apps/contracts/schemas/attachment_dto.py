from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AttachFileRequest(BaseModel):
    file_id: uuid.UUID


class AttachmentResponse(BaseModel):
    file_id: uuid.UUID
    name: str
    size_bytes: int
    status: str
    download_url: Optional[str] = None
    attached_by: uuid.UUID
    attached_at: datetime

    model_config = {"from_attributes": True}


class AttachmentListResponse(BaseModel):
    results: list[AttachmentResponse]
    count: int
