import pytest
from django.test import Client

from apps.common.tenancy.context import clear_tenant_context


@pytest.mark.django_db
class TestFailClosedSecurityBoundary:
    def test_private_route_fails_closed_when_context_is_corrupted(self):
        client = Client()
        clear_tenant_context()

        response = client.get(
            "/api/v1/contracts/dashboard_stub",
            HTTP_X_GATEWAY_TOKEN="complect_internal_secure_mesh_token_2026",
        )

        assert response.status_code == 401
        assert b"AUTH_GATE_DENIED" in response.content
