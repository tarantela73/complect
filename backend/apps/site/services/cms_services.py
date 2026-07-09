from __future__ import annotations

import uuid

from django.db import transaction

from apps.common.context.core import get_tenant_id
from apps.files.services.storage_backend import get_storage_backend
from apps.site.models import SiteImage
from apps.site.schemas.cms_dto import (
    CmsImageResponse,
    GalleryConfirmRequest,
    GalleryReorderItem,
    GalleryUpdateRequest,
    PresignedUploadResponse,
)


def _image_to_response(img: SiteImage) -> CmsImageResponse:
    return CmsImageResponse(
        id=img.id,
        section=img.section,
        s3_key=img.s3_key,
        caption=img.caption,
        alt=img.alt,
        order=img.order,
        file_record_id=img.file_record_id,
        created_at=img.created_at,
    )


class GalleryService:
    @staticmethod
    def request_upload(
        *,
        original_filename: str,
        content_type: str = "image/jpeg",
        size_bytes: int = 0,
    ) -> PresignedUploadResponse:
        s3_key = f"cms/gallery/{uuid.uuid4()}/{original_filename}"
        backend = get_storage_backend()
        url = backend.generate_upload_url(
            object_key=s3_key,
            content_type=content_type,
            size_bytes=size_bytes,
            expires_in=3600,
        )
        return PresignedUploadResponse(upload_url=url, s3_key=s3_key, expires_in=3600)

    @staticmethod
    def confirm_upload(payload: GalleryConfirmRequest) -> SiteImage:
        tenant_id = get_tenant_id()
        img = SiteImage(
            tenant_id=tenant_id,
            section=payload.section,
            s3_key=payload.s3_key,
            caption=payload.caption,
            alt=payload.alt,
            order=payload.order,
            file_record_id=payload.file_record_id,
        )
        img.save()
        return img

    @staticmethod
    def update_image(image_id: uuid.UUID, payload: GalleryUpdateRequest) -> SiteImage:
        tenant_id = get_tenant_id()
        img = SiteImage.objects.filter(
            id=image_id,
            tenant_id=tenant_id,
            is_deleted=False,
        ).first()
        if img is None:
            return None  # type: ignore[return-value]

        update_data = payload.model_dump(exclude_none=True)
        for field, value in update_data.items():
            setattr(img, field, value)
        img.save(update_fields=list(update_data.keys()) + ["updated_at"])
        return img

    @staticmethod
    def delete_image(image_id: uuid.UUID) -> bool:
        tenant_id = get_tenant_id()
        updated = SiteImage.objects.filter(
            id=image_id,
            tenant_id=tenant_id,
            is_deleted=False,
        ).update(is_deleted=True)
        return updated > 0

    @staticmethod
    def reorder(items: list[GalleryReorderItem]) -> int:
        tenant_id = get_tenant_id()
        with transaction.atomic():
            updated = 0
            for item in items:
                count = SiteImage.objects.filter(
                    id=item.id,
                    tenant_id=tenant_id,
                    is_deleted=False,
                ).update(order=item.order)
                updated += count
        return updated
