import uuid
from datetime import datetime, timezone

import pytest
from django.test import Client, override_settings

from apps.site.models.site_content import PagePublicationStatus, SitePageContent

TENANT_ID = uuid.uuid4()
PUBLIC_TENANT_STR = str(TENANT_ID)
BASE_URL = "https://test.example.com"


def _make_page(slug: str, status: str) -> SitePageContent:
    return SitePageContent.objects.create(
        tenant_id=TENANT_ID,
        slug=slug,
        status=status,
        content={"type": "doc"},
        meta={},
    )


@pytest.mark.django_db
class TestSitemapXml:
    def test_sitemap_returns_200_xml(self):
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_STR, PUBLIC_SITE_BASE_URL=BASE_URL):
            resp = Client().get("/sitemap.xml")
        assert resp.status_code == 200
        assert "application/xml" in resp["Content-Type"]

    def test_sitemap_includes_only_published_pages(self):
        _make_page("home", PagePublicationStatus.PUBLISHED)
        _make_page("about", PagePublicationStatus.PUBLISHED)
        _make_page("draft-page", PagePublicationStatus.DRAFT)
        _make_page("scheduled-page", PagePublicationStatus.SCHEDULED)

        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_STR, PUBLIC_SITE_BASE_URL=BASE_URL):
            resp = Client().get("/sitemap.xml")

        content = resp.content.decode()
        assert "home" in content or BASE_URL in content
        assert "about" in content
        assert "draft-page" not in content
        assert "scheduled-page" not in content

    def test_sitemap_home_slug_uses_root_url(self):
        _make_page("home", PagePublicationStatus.PUBLISHED)
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_STR, PUBLIC_SITE_BASE_URL=BASE_URL):
            resp = Client().get("/sitemap.xml")
        content = resp.content.decode()
        assert f"{BASE_URL}/" in content
        assert f"{BASE_URL}/home/" not in content

    def test_sitemap_valid_xml_structure(self):
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_STR, PUBLIC_SITE_BASE_URL=BASE_URL):
            resp = Client().get("/sitemap.xml")
        content = resp.content.decode()
        assert '<?xml version="1.0"' in content
        assert "sitemaps.org" in content
        assert "<urlset" in content


@pytest.mark.django_db
class TestRobotsTxt:
    def test_robots_returns_200_text(self):
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_STR, PUBLIC_SITE_BASE_URL=BASE_URL):
            resp = Client().get("/robots.txt")
        assert resp.status_code == 200
        assert "text/plain" in resp["Content-Type"]

    def test_robots_contains_sitemap_reference(self):
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_STR, PUBLIC_SITE_BASE_URL=BASE_URL):
            resp = Client().get("/robots.txt")
        content = resp.content.decode()
        assert f"Sitemap: {BASE_URL}/sitemap.xml" in content
        assert "User-agent: *" in content


@pytest.mark.django_db
class TestPublicApiStatusFilter:
    def test_draft_page_not_visible_via_public_api(self):
        SitePageContent.objects.create(
            tenant_id=TENANT_ID,
            slug="secret-draft",
            status=PagePublicationStatus.DRAFT,
            content={"type": "doc", "secret": True},
            meta={},
        )
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_STR):
            resp = Client().get("/api/v1/public/cms/secret-draft/")
        assert resp.status_code == 200
        # Graceful degradation: returns empty content for missing/unpublished pages
        assert resp.json()["content"] == {}

    def test_scheduled_page_not_visible_via_public_api(self):
        SitePageContent.objects.create(
            tenant_id=TENANT_ID,
            slug="upcoming",
            status=PagePublicationStatus.SCHEDULED,
            content={"type": "doc"},
            meta={},
        )
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_STR):
            resp = Client().get("/api/v1/public/cms/upcoming/")
        assert resp.json()["content"] == {}

    def test_published_page_visible_via_public_api(self):
        SitePageContent.objects.create(
            tenant_id=TENANT_ID,
            slug="published-page",
            status=PagePublicationStatus.PUBLISHED,
            content={"type": "doc", "content": []},
            meta={"title": "Главная"},
        )
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_STR):
            resp = Client().get("/api/v1/public/cms/published-page/")
        assert resp.json()["content"]["type"] == "doc"
        assert resp.json()["meta"]["title"] == "Главная"
