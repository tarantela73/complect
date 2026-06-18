import uuid
from typing import Optional

from django.http import HttpRequest
from ninja import Query, Router

from apps.common.api.errors import make_error_response
from apps.common.api.permissions import require_permission
from apps.site.queries.cms_queries import get_cms_gallery, get_cms_page_for_editing
from apps.site.schemas.cms_dto import (
    CmsImageListResponse,
    CmsImageResponse,
    CmsPageContentResponse,
    CmsPageUpdateRequest,
)
from apps.site.services.cms_publish_service import (
    CmsPublishService,
    PageAlreadyPublishedError,
    PageNotFoundError,
)
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
        order=img.order,
        created_at=img.created_at,
    )
