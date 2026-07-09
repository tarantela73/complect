from apps.common.context.core import get_tenant_id
from apps.site.models import SitePageContent
from apps.site.models.site_content import PagePublicationStatus
from apps.site.schemas.public_dto import PublicPageContentResponse


def get_page_content(slug: str) -> PublicPageContentResponse:
    """Returns CMS content for a slug. Only PUBLISHED pages are visible. Returns empty on miss."""
    tenant_id = get_tenant_id()
    page = SitePageContent.objects.filter(
        tenant_id=tenant_id,
        slug=slug,
        status=PagePublicationStatus.PUBLISHED,
        is_deleted=False,
    ).first()
    if page is None:
        return PublicPageContentResponse(slug=slug, content={}, meta={})
    return PublicPageContentResponse(slug=slug, content=page.content, meta=page.meta)
