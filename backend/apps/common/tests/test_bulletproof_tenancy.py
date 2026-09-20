# backend/apps/common/tests/test_bulletproof_tenancy.py
import pytest
from django.test import Client


@pytest.mark.django_db
class TestBulletproofTenancyBoundary:
    def test_middleware_blocks_direct_calls_without_gateway_token(self):
        """[LEVEL 8: SECURITY] Проверяет блокировку Trust Boundary при обходе Kong шлюза."""
        client = Client()

        # Стучимся с валидными ID, но БЕЗ секретного токена шлюза X-Gateway-Token
        headers = {
            "HTTP_X_TENANT_ID": "11111111-2222-3333-4444-555555555555",
            "HTTP_X_USER_ID": "66666666-7777-8888-9999-000000000000",
        }
        response = client.get("/api/v1/contracts/dashboard_stub", **headers)

        # Бэкенд обязан жестко выкинуть 403 Forbidden (Байпас заблокирован)
        assert response.status_code == 403
        assert b"GATEWAY_TRUST_VIOLATION" in response.content

    def test_middleware_rejects_unauthenticated_spoofed_nil_uuid_headers(self):
        """[LEVEL 8: SECURITY] Nil-UUID gateway headers never become request authority."""
        client = Client()

        headers = {
            "HTTP_X_GATEWAY_TOKEN": "complect_internal_secure_mesh_token_2026",
            "HTTP_X_TENANT_ID": "00000000-0000-0000-0000-000000000000",
            "HTTP_X_USER_ID": "66666666-7777-8888-9999-000000000000",
        }
        response = client.get("/api/v1/contracts/dashboard_stub", **headers)

        assert response.status_code == 401
        assert b"AUTH_GATE_DENIED" in response.content
