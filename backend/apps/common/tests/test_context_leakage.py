import uuid

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client

from apps.common.context.core import (
    get_correlation_id,
    get_request_id,
    get_tenant_id,
    get_user_id,
    is_primary_db_enforced,
)


# [ИСПРАВЛЕНО]: Жесткий ИБ-заслон. Принудительно взводим маршрутизатор config.urls для тест-раннера
@pytest.mark.urls("config.urls")
class TestTransportContextIsolation:
    def test_context_cleanup_after_request(self):
        """[TEST 1: CLEANUP] Гарантирует тотальное выжигание ВСЕХ ContextVar из потока."""
        client = Client()
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()

        headers = {
            "HTTP_X_TENANT_ID": str(tenant_id),
            "HTTP_X_USER_ID": str(user_id),
            "HTTP_X_REQUEST_ID": str(uuid.uuid4()),
            "HTTP_X_CORRELATION_ID": str(uuid.uuid4()),
            "HTTP_X_GATEWAY_TOKEN": "complect_internal_secure_mesh_token_2026",
        }

        response = client.get("/api/v1/contracts/", **headers)
        assert response.status_code == 200

        # Strict-верификация PermissionDenied на каждую контекстную переменную
        with pytest.raises(PermissionDenied):
            get_tenant_id()

        with pytest.raises(PermissionDenied):
            get_user_id()

        with pytest.raises(PermissionDenied):
            get_request_id()

        with pytest.raises(PermissionDenied):
            get_correlation_id()

        assert is_primary_db_enforced() is False

    def test_protected_route_requires_context_headers(self):
        """[TEST 2: FAIL-CLOSED] Проверяет защиту доменного периметра (401)."""
        client = Client()
        response = client.get(
            "/api/v1/contracts/",
            HTTP_X_GATEWAY_TOKEN="complect_internal_secure_mesh_token_2026",
        )
        assert response.status_code == 401
        assert response.json()["code"] == "AUTH_GATE_DENIED"

    def test_malformed_headers_rejected(self):
        """[TEST 3: MALFORMED UUID] Проверяет отсечку битых UUID в заголовках (400)."""
        client = Client()
        headers = {
            "HTTP_X_TENANT_ID": "INVALID-UUID-STRING",
            "HTTP_X_USER_ID": "NOT-A-VALID-UUID",
            "HTTP_X_GATEWAY_TOKEN": "complect_internal_secure_mesh_token_2026",
        }
        response = client.get("/api/v1/contracts/", **headers)
        assert response.status_code == 400
        assert response.json()["code"] == "MALFORMED_CONTEXT_HEADERS"

    def test_malformed_correlation_id_rejected(self):
        """[TEST 4: MALFORMED CORRELATION ID] Верифицирует валидацию UUID для Correlation-ID (400)."""
        client = Client()
        headers = {
            "HTTP_X_TENANT_ID": str(uuid.uuid4()),
            "HTTP_X_USER_ID": str(uuid.uuid4()),
            "HTTP_X_REQUEST_ID": str(uuid.uuid4()),
            "HTTP_X_CORRELATION_ID": "BROKEN-NON-UUID-VALUE",
            "HTTP_X_GATEWAY_TOKEN": "complect_internal_secure_mesh_token_2026",
        }
        response = client.get("/api/v1/contracts/", **headers)
        assert response.status_code == 400
        assert response.json()["code"] == "MALFORMED_CONTEXT_HEADERS"

    def test_context_isolation_between_requests(self):
        """[TEST 5: LEAK PREVENTION] Гарантирует изоляцию памяти между последовательными запросами."""
        client = Client()
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()

        response_a = client.get(
            "/api/v1/contracts/",
            HTTP_X_TENANT_ID=str(tenant_a),
            HTTP_X_USER_ID=str(uuid.uuid4()),
            HTTP_X_REQUEST_ID=str(uuid.uuid4()),
            HTTP_X_CORRELATION_ID=str(uuid.uuid4()),
            HTTP_X_GATEWAY_TOKEN="complect_internal_secure_mesh_token_2026",
        )
        assert response_a.status_code == 200

        response_b = client.get(
            "/api/v1/contracts/",
            HTTP_X_TENANT_ID=str(tenant_b),
            HTTP_X_USER_ID=str(uuid.uuid4()),
            HTTP_X_REQUEST_ID=str(uuid.uuid4()),
            HTTP_X_CORRELATION_ID=str(uuid.uuid4()),
            HTTP_X_GATEWAY_TOKEN="complect_internal_secure_mesh_token_2026",
        )
        assert response_b.status_code == 200

        with pytest.raises(PermissionDenied):
            get_tenant_id()

    def test_options_bypass(self):
        """[TEST 6: CORS PREFLIGHT BYPASS] Проверяет свободный пропуск OPTIONS-запросов."""
        client = Client()
        response = client.options("/api/v1/contracts/")
        assert response.status_code != 401

    def test_public_routes_bypass(self):
        """[TEST 7: PUBLIC BYPASS] Подтверждает легитимный доступ K8s проб к /health/ и /public/ping/."""
        client = Client()
        response_health = client.get("/health/")
        response_ping = client.get("/public/ping/")

        assert response_health.status_code == 200
        assert response_ping.status_code == 200

    def test_primary_flag_cleanup(self):
        """[TEST 8: PRIMARY FLAG CLEANUP] Проверяет сброс флага Command Side базы в False."""
        client = Client()
        headers = {
            "HTTP_X_TENANT_ID": str(uuid.uuid4()),
            "HTTP_X_USER_ID": str(uuid.uuid4()),
            "HTTP_X_REQUEST_ID": str(uuid.uuid4()),
            "HTTP_X_CORRELATION_ID": str(uuid.uuid4()),
            "HTTP_X_GATEWAY_TOKEN": "complect_internal_secure_mesh_token_2026",
        }
        response = client.get("/api/v1/contracts/", **headers)
        assert response.status_code == 200
        assert is_primary_db_enforced() is False
