# backend/apps/common/tests/test_public_middleware.py
"""
Tests for PUBLIC_API_PREFIX branch in AsgiTenantEnforcementMiddleware (ADR-0023).
Tenant is injected from settings.PUBLIC_SITE_TENANT_ID without any JWT or gateway token.
"""
import pytest
from django.test import Client, override_settings

PUBLIC_TENANT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

# A real API path that goes through the standard (non-public) auth branch
PRIVATE_PATH = "/api/v1/requests/"


@pytest.mark.django_db
class TestPublicMiddlewareTenantInjection:
    def test_public_prefix_returns_not_gateway_error(self):
        """
        GET /api/v1/public/... without any headers must NOT return GATEWAY_TRUST_VIOLATION.
        The middleware injects tenant and forwards the request.
        404 from the router is acceptable — it proves the request reached Django routing.
        """
        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            response = client.get("/api/v1/public/cms/home/")
        # Must not be 403 GATEWAY_TRUST_VIOLATION or 401 AUTH_GATE_DENIED
        assert response.status_code not in (401, 403)

    def test_private_path_without_gateway_token_returns_403(self):
        """Non-public paths still require gateway token."""
        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            response = client.get(PRIVATE_PATH)
        assert response.status_code == 403
        assert b"GATEWAY_TRUST_VIOLATION" in response.content

    def test_public_prefix_does_not_require_jwt(self):
        """Confirm no AUTH_GATE_DENIED on public prefix — JWT is not inspected."""
        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            response = client.post(
                "/api/v1/public/requests/",
                data={},
                content_type="application/json",
            )
        # Validation error (422) or routing 404 — not an auth denial
        assert response.status_code not in (401, 403)
