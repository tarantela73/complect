import uuid

from apps.common.context.core import get_tenant_id
from apps.site.models import SiteImage, SitePageContent
from apps.site.schemas.cms_dto import CmsImageResponse, CmsPageContentResponse


class CmsUpdateService:
    @staticmethod
    def upsert_page(slug: str, content: dict | None, meta: dict | None) -> SitePageContent:
        tenant_id = get_tenant_id()
        defaults: dict = {}
        if content is not None:
            defaults["content"] = content
        if meta is not None:
            defaults["meta"] = meta

        page, _ = SitePageContent.objects.update_or_create(
            tenant_id=tenant_id,
            slug=slug,
            defaults=defaults,
        )
        return page

    @staticmethod
    def create_image(section: str, s3_key: str, caption: str = "", order: int = 0) -> SiteImage:
        tenant_id = get_tenant_id()
        img = SiteImage(
            tenant_id=tenant_id,
            section=section,
            s3_key=s3_key,
            caption=caption,
            order=order,
        )
        img.save()
        return img
