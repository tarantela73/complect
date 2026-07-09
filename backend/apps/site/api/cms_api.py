import uuid
from typing import Optional

from django.http import HttpRequest
from ninja import Query, Router

from apps.common.api.errors import make_error_response
from apps.common.api.permissions import require_permission
from apps.common.context.core import get_tenant_id, get_user_id
from apps.site.queries.cms_queries import get_cms_gallery, get_cms_page_for_editing
from apps.site.schemas.cms_dto import (
    CmsImageListResponse,
    CmsImageResponse,
    CmsPageContentResponse,
    CmsPageUpdateRequest,
    GalleryConfirmRequest,
    GalleryReorderRequest,
    GalleryUpdateRequest,
    PresignedUploadResponse,
    RevisionListResponse,
    RevisionResponse,
)
from apps.site.services.cms_publish_service import (
    CmsPublishService,
    PageAlreadyPublishedError,
    PageNotFoundError,
)
from apps.site.services.cms_revision_service import (
    CmsRevisionService,
    RevisionNotFoundError,
)
from apps.site.services.cms_revision_service import (
    PageNotFoundError as RevisionPageNotFoundError,
)
from apps.site.services.cms_services import GalleryService
from apps.site.services.cms_update_service import CmsUpdateService

router = Router(tags=["cms"])


@router.get("/pages/{slug}/", response={200: CmsPageContentResponse, 404: dict})
@require_permission("read", "cms")
def get_cms_page(request: HttpRequest, slug: str):
    page = get_cms_page_for_editing(slug)
    if page is None:
        return 404, make_error_response("CMS_PAGE_NOT_FOUND", f"Страница '{slug}' не найдена.")
    return 200, page


@router.patch("/pages/{slug}/", response={200: CmsPageContentResponse})
@require_permission("write", "cms")
def update_cms_page(request: HttpRequest, slug: str, body: CmsPageUpdateRequest):
    page = CmsUpdateService.upsert_page(
        slug=slug,
        content=body.content.model_dump() if body.content is not None else None,
        meta_update=body.meta,
    )
    return 200, CmsPageContentResponse(
        id=page.id,
        slug=page.slug,
        content=page.content,
        meta=page.meta,
        status=page.status,
        updated_at=page.updated_at,
    )


@router.post("/pages/{slug}/publish/", response={200: CmsPageContentResponse, 404: dict, 409: dict})
@require_permission("publish", "cms")
def publish_cms_page(request: HttpRequest, slug: str):
    try:
        page = CmsPublishService.publish_page(slug=slug)
    except PageNotFoundError:
        return 404, make_error_response("CMS_PAGE_NOT_FOUND", f"Страница '{slug}' не найдена.")
    except PageAlreadyPublishedError:
        return 409, make_error_response("CMS_PAGE_ALREADY_PUBLISHED", f"Страница '{slug}' уже опубликована.")
    return 200, CmsPageContentResponse(
        id=page.id,
        slug=page.slug,
        content=page.content,
        meta=page.meta,
        status=page.status,
        updated_at=page.updated_at,
    )


@router.get(
    "/pages/{slug}/revisions/",
    response={200: RevisionListResponse, 404: dict},
)
@require_permission("read", "cms")
def list_page_revisions(
    request: HttpRequest,
    slug: str,
    cursor: Optional[int] = Query(None),
    limit: int = Query(20),
):
    tenant_id = get_tenant_id()
    from apps.site.models import SitePageContent

    page = SitePageContent.objects.filter(tenant_id=tenant_id, slug=slug, is_deleted=False).first()
    if page is None:
        return 404, make_error_response("CMS_PAGE_NOT_FOUND", f"Страница '{slug}' не найдена.")

    svc = CmsRevisionService()
    rows = list(
        svc.list_revisions(
            page_id=page.id,
            tenant_id=tenant_id,
            cursor=cursor,
            limit=min(limit, 100),
        )
    )

    page_size = min(limit, 100)
    has_next = len(rows) > page_size
    results = rows[:page_size]
    next_cursor = results[-1].revision_number if has_next and results else None

    return 200, RevisionListResponse(
        results=[
            RevisionResponse(
                id=r.id,
                revision_number=r.revision_number,
                status=r.status,
                label=r.label,
                created_at=r.created_at,
                created_by_id=r.created_by_id,
            )
            for r in results
        ],
        next_cursor=next_cursor,
    )


@router.post(
    "/pages/{slug}/revisions/{revision_id}/rollback/",
    response={200: CmsPageContentResponse, 404: dict},
)
@require_permission("write", "cms")
def rollback_page_revision(
    request: HttpRequest,
    slug: str,
    revision_id: uuid.UUID,
):
    tenant_id = get_tenant_id()
    user_id = get_user_id()

    from apps.site.models import SitePageContent

    page = SitePageContent.objects.filter(tenant_id=tenant_id, slug=slug, is_deleted=False).first()
    if page is None:
        return 404, make_error_response("CMS_PAGE_NOT_FOUND", f"Страница '{slug}' не найдена.")

    svc = CmsRevisionService()
    try:
        updated_page = svc.rollback(
            page_id=page.id,
            revision_id=revision_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
    except RevisionNotFoundError:
        return 404, make_error_response(
            "REVISION_NOT_FOUND",
            f"Версия {revision_id} не найдена.",
        )
    except RevisionPageNotFoundError:
        return 404, make_error_response("CMS_PAGE_NOT_FOUND", f"Страница '{slug}' не найдена.")

    return 200, CmsPageContentResponse(
        id=updated_page.id,
        slug=updated_page.slug,
        content=updated_page.content,
        meta=updated_page.meta,
        status=updated_page.status,
        updated_at=updated_page.updated_at,
    )


@router.get("/gallery/", response={200: CmsImageListResponse})
@require_permission("read", "cms")
def list_gallery(request: HttpRequest, section: Optional[str] = Query(None)):
    images = get_cms_gallery(section=section)
    return 200, CmsImageListResponse(results=images)


@router.post("/gallery/", response={201: CmsImageResponse, 400: dict})
@require_permission("write", "cms")
def create_gallery_image(
    request: HttpRequest,
    section: str,
    s3_key: str,
    caption: str = "",
    order: int = 0,
):
    if not s3_key.strip():
        return 400, make_error_response("INVALID_S3_KEY", "s3_key cannot be empty.")
    img = CmsUpdateService.create_image(
        section=section,
        s3_key=s3_key,
        caption=caption,
        order=order,
    )
    return 201, CmsImageResponse(
        id=img.id,
        section=img.section,
        s3_key=img.s3_key,
        caption=img.caption,
        alt=img.alt,
        order=img.order,
        file_record_id=img.file_record_id,
        created_at=img.created_at,
    )


@router.post("/gallery/request-upload/", response={200: PresignedUploadResponse, 400: dict})
@require_permission("write", "cms")
def request_gallery_upload(
    request: HttpRequest,
    filename: str,
    content_type: str = "image/jpeg",
    size_bytes: int = 0,
):
    if not filename.strip():
        return 400, make_error_response("INVALID_FILENAME", "filename cannot be empty.")
    result = GalleryService.request_upload(
        original_filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
    )
    return 200, result


@router.post("/gallery/confirm-upload/", response={201: CmsImageResponse, 400: dict})
@require_permission("write", "cms")
def confirm_gallery_upload(request: HttpRequest, body: GalleryConfirmRequest):
    if not body.s3_key.strip():
        return 400, make_error_response("INVALID_S3_KEY", "s3_key cannot be empty.")
    img = GalleryService.confirm_upload(body)
    return 201, CmsImageResponse(
        id=img.id,
        section=img.section,
        s3_key=img.s3_key,
        caption=img.caption,
        alt=img.alt,
        order=img.order,
        file_record_id=img.file_record_id,
        created_at=img.created_at,
    )


@router.post("/gallery/reorder/", response={200: dict})
@require_permission("write", "cms")
def reorder_gallery(request: HttpRequest, body: GalleryReorderRequest):
    updated = GalleryService.reorder(items=body.items)
    return 200, {"updated": updated}


@router.patch("/gallery/{image_id}/", response={200: CmsImageResponse, 404: dict})
@require_permission("write", "cms")
def update_gallery_image(request: HttpRequest, image_id: uuid.UUID, body: GalleryUpdateRequest):
    img = GalleryService.update_image(image_id=image_id, payload=body)
    if img is None:
        return 404, make_error_response("IMAGE_NOT_FOUND", "Изображение не найдено.")
    return 200, CmsImageResponse(
        id=img.id,
        section=img.section,
        s3_key=img.s3_key,
        caption=img.caption,
        alt=img.alt,
        order=img.order,
        file_record_id=img.file_record_id,
        created_at=img.created_at,
    )


@router.delete("/gallery/{image_id}/", response={204: None, 404: dict})
@require_permission("write", "cms")
def delete_gallery_image(request: HttpRequest, image_id: uuid.UUID):
    deleted = GalleryService.delete_image(image_id=image_id)
    if not deleted:
        return 404, make_error_response("IMAGE_NOT_FOUND", "Изображение не найдено.")
    return 204, None
