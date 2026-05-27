import pytest
from django.test import Client
from apps.common.tenancy.context import clear_tenant_context


@pytest.mark.django_db
class TestFailClosedSecurityBoundary:
    def test_private_route_fails_closed_when_context_is_corrupted(self):
        """[LEVEL 8: SECURITY] Проверяет блокировку Fail-Closed: сбой SETUP_SESSION гасит доступ."""
        client = Client()

        # Принудительно очищаем контекст Python слоя перед запросом
        clear_tenant_context()

        # Стучимся на закрытый эндпоинт БЕЗ инжекции X-Tenant-ID заголовков
        response = client.get("/api/v1/contracts/dashboard_stub")

        # Перехватчик AsgiTenantEnforcementMiddleware обязан выдать 401 заслон
        assert response.status_code == 401
        assert b"AUTH_GATE_DENIED" in response.content