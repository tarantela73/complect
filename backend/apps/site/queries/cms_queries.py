
from apps.common.context.core import get_tenant_id
from apps.site.models import SiteImage, SitePageContent
from apps.site.schemas.cms_dto import CmsImageResponse, CmsPageContentResponse


def get_cms_page_for_editing(slug: str) -> CmsPageContentResponse | None:
    tenant_id = get_tenant_id()
    page = (
        SitePageContent.objects
        .filter(tenant_id=tenant_id, slug=slug, is_deleted=False)
        .first()
    )
    if page is None:
        return None
    return CmsPageContentResponse(
        id=page.id,
        slug=page.slug,
        content=page.content,
        meta=page.meta,
        status=page.status,
        updated_at=page.updated_at,
    )


def get_cms_gallery(section: str | None = None) -> list[CmsImageResponse]:
    tenant_id = get_tenant_id()
    qs = SiteImage.objects.filter(tenant_id=tenant_id, is_deleted=False)
    if section:
        qs = qs.filter(section=section)
    return [
        CmsImageResponse(
            id=img.id,
            section=img.section,
            s3_key=img.s3_key,
            caption=img.caption,
            alt=img.alt,
            order=img.order,
            file_record_id=img.file_record_id,
            created_at=img.created_at,
        )
        for img in qs
    ]
