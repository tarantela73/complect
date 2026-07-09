# backend/apps/site/tests/test_public_api.py
import json
import uuid
from unittest.mock import patch

import pytest
from django.test import Client, override_settings

PUBLIC_TENANT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _public_post(client, path, data):
    return client.post(
        path,
        data=json.dumps(data),
        content_type="application/json",
    )


@pytest.mark.django_db
class TestGetCmsPage:
    def test_returns_empty_content_when_no_record(self):
        """Graceful degradation: slug not in DB → empty content, 200 OK."""
        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            response = client.get("/api/v1/public/cms/home/")
        assert response.status_code == 200
        data = response.json()
        assert data["slug"] == "home"
        assert data["content"] == {}
        assert data["meta"] == {}

    def test_returns_content_when_record_exists(self):
        """After creating PUBLISHED SitePageContent, GET returns its content."""
        from apps.site.models import SitePageContent
        from apps.site.models.site_content import PagePublicationStatus

        SitePageContent.objects.create(
            tenant_id=uuid.UUID(PUBLIC_TENANT_ID),
            slug="about",
            status=PagePublicationStatus.PUBLISHED,
            content={"type": "doc", "content": []},
            meta={"title": "О компании"},
        )

        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            response = client.get("/api/v1/public/cms/about/")
        assert response.status_code == 200
        data = response.json()
        assert data["slug"] == "about"
        assert data["content"]["type"] == "doc"
        assert data["meta"]["title"] == "О компании"


@pytest.mark.django_db
class TestSubmitLeadForm:
    VALID_PAYLOAD = {
        "client_name": "Иван Иванов",
        "client_phone": "+79001234567",
        "address": "г. Москва, ул. Тестовая, 1",
        "resource_type": "ELECTRICITY",
        "cf_turnstile_token": "valid-token",
    }

    def test_valid_form_creates_request(self):
        """AE-2: valid data + Turnstile success → 201, Request created with source=WEBSITE."""
        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            with patch("apps.site.api.public_api.verify_turnstile", return_value=True):
                response = _public_post(client, "/api/v1/public/requests/", self.VALID_PAYLOAD)

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["status"] == "NEW"

        from apps.requests.models.request import Request

        req = Request.objects.get(id=data["id"])
        assert req.source == "WEBSITE"
        assert str(req.tenant_id) == PUBLIC_TENANT_ID

    def test_turnstile_failure_returns_422(self):
        """AE-4: Turnstile returns false → 422 CAPTCHA_FAILED."""
        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            with patch("apps.site.api.public_api.verify_turnstile", return_value=False):
                response = _public_post(client, "/api/v1/public/requests/", self.VALID_PAYLOAD)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CAPTCHA_FAILED"

    def test_missing_client_name_returns_400(self):
        """AE-3: empty client_name → Pydantic validation error → 400."""
        client = Client()
        payload = {**self.VALID_PAYLOAD, "client_name": ""}
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            with patch("apps.site.api.public_api.verify_turnstile", return_value=True):
                response = _public_post(client, "/api/v1/public/requests/", payload)

        assert response.status_code == 400

    def test_turnstile_timeout_returns_503(self):
        """httpx.TimeoutException → 503 EXTERNAL_SERVICE_UNAVAILABLE."""
        import httpx

        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            with patch(
                "apps.site.api.public_api.verify_turnstile",
                side_effect=httpx.TimeoutException("timeout"),
            ):
                response = _public_post(client, "/api/v1/public/requests/", self.VALID_PAYLOAD)

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "EXTERNAL_SERVICE_UNAVAILABLE"

    def test_sewage_resource_type_accepted(self):
        """New ResourceType SEWAGE accepted without errors."""
        client = Client()
        payload = {**self.VALID_PAYLOAD, "resource_type": "SEWAGE"}
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            with patch("apps.site.api.public_api.verify_turnstile", return_value=True):
                response = _public_post(client, "/api/v1/public/requests/", payload)
        assert response.status_code == 201
