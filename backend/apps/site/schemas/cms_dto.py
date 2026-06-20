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
    alt: str = ""
    order: int
    file_record_id: uuid.UUID | None = None
    created_at: datetime


class CmsImageListResponse(BaseModel):
    results: list[CmsImageResponse]


class RevisionResponse(BaseModel):
    id: uuid.UUID
    revision_number: int
    status: str
    label: str
    created_at: datetime
    created_by_id: uuid.UUID


class RevisionDetailResponse(RevisionResponse):
    content: dict
    meta: dict


class RevisionListResponse(BaseModel):
    results: list[RevisionResponse]
    next_cursor: int | None = None


class GalleryUpdateRequest(BaseModel):
    caption: str | None = None
    alt: str | None = None
    order: int | None = None
    section: str | None = None


class GalleryReorderItem(BaseModel):
    id: uuid.UUID
    order: int


class GalleryReorderRequest(BaseModel):
    items: list[GalleryReorderItem]


class PresignedUploadResponse(BaseModel):
    upload_url: str
    s3_key: str
    expires_in: int


class GalleryConfirmRequest(BaseModel):
    s3_key: str
    section: str
    caption: str = ""
    alt: str = ""
    order: int = 0
    file_record_id: uuid.UUID | None = None
