from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ContragentAttachmentResponse(BaseModel):
    file_id: uuid.UUID
    name: str
    size_bytes: int
    status: str
    download_url: Optional[str] = None
    attached_by: uuid.UUID
    attached_at: datetime


class ContragentAttachmentListResponse(BaseModel):
    results: list[ContragentAttachmentResponse]
    count: int


class ContragentAttachFileRequest(BaseModel):
    file_id: uuid.UUID


class ContragentTimelineEntrySchema(BaseModel):
    event_id: uuid.UUID
    event_type: str
    label: str
    occurred_at: str
    user_id: Optional[uuid.UUID] = None
    correlation_id: Optional[uuid.UUID] = None


class ContragentTimelineResponse(BaseModel):
    results: list[ContragentTimelineEntrySchema]
    count: int
