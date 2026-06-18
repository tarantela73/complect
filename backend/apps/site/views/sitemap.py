from __future__ import annotations

import uuid

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from apps.site.models.site_content import PagePublicationStatus, SitePageContent

_SITEMAP_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{entries}
</urlset>"""

_URL_ENTRY = """\
  <url>
    <loc>{loc}</loc>
    <lastmod>{lastmod}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>"""


def sitemap_xml(request: HttpRequest) -> HttpResponse:
    tenant_id = uuid.UUID(str(settings.PUBLIC_SITE_TENANT_ID))
    base_url = settings.PUBLIC_SITE_BASE_URL.rstrip("/")

    pages = SitePageContent.objects.filter(
        tenant_id=tenant_id,
        status=PagePublicationStatus.PUBLISHED,
        is_deleted=False,
    ).only("slug", "updated_at")

    entries = []
    for page in pages:
        loc = f"{base_url}/{page.slug}/" if page.slug != "home" else f"{base_url}/"
        lastmod = page.updated_at.strftime("%Y-%m-%d")
        entries.append(_URL_ENTRY.format(loc=loc, lastmod=lastmod))

    xml = _SITEMAP_TEMPLATE.format(entries="\n".join(entries))
    return HttpResponse(xml, content_type="application/xml; charset=utf-8")


def robots_txt(request: HttpRequest) -> HttpResponse:
    base_url = getattr(settings, "PUBLIC_SITE_BASE_URL", "").rstrip("/")
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {base_url}/sitemap.xml\n"
    )
    return HttpResponse(content, content_type="text/plain; charset=utf-8")
