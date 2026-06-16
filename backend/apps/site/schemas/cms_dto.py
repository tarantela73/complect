import uuid
from datetime import datetime

from pydantic import BaseModel


class CmsPageContentResponse(BaseModel):
    id: uuid.UUID
    slug: str
    content: dict
    meta: dict
    updated_at: datetime


class CmsPageUpdateRequest(BaseModel):
    content: dict | None = None
    meta: dict | None = None


class CmsImageResponse(BaseModel):
    id: uuid.UUID
    section: str
    s3_key: str
    caption: str
    order: int
    created_at: datetime


class CmsImageListResponse(BaseModel):
    results: list[CmsImageResponse]
