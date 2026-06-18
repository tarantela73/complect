import uuid
from datetime import datetime

from pydantic import BaseModel

from apps.site.schemas.meta_schema import MetaUpdateRequest
from apps.site.schemas.tiptap_schema import TiptapDocSchema


class CmsPageContentResponse(BaseModel):
    id: uuid.UUID
    slug: str
    content: dict
    meta: dict
    status: str | None = None
    updated_at: datetime


class CmsPageUpdateRequest(BaseModel):
    content: TiptapDocSchema | None = None
    meta: MetaUpdateRequest | None = None


class CmsImageResponse(BaseModel):
    id: uuid.UUID
    section: str
    s3_key: str
    caption: str
    order: int
    created_at: datetime


class CmsImageListResponse(BaseModel):
    results: list[CmsImageResponse]
