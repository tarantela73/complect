import pytest
import uuid
from django.test import Client
from apps.common.tenancy.context import is_primary_db_enforced, _current_request_context


class TestTransportsContextIsolation:
    def test_middleware_pipeline_forces_absolute_contextvars_cleanup(self):
        """[LEVEL 8: SECURITY] Гарантирует полную очистку контекста сессии, исключая утечку данных между воркерами."""
        client = Client()
        tenant_alpha = uuid.uuid4()
        user_alpha = uuid.uuid4()

        # Настраиваем заголовки легитимного запроса
        headers = {
            "HTTP_X_TENANT_ID": str(tenant_alpha),
            "HTTP_X_USER_ID": str(user_alpha),
            "HTTP_X_REQUEST_ID": str(uuid.uuid4()),
            "HTTP_X_CORRELATION_ID": str(uuid.uuid4()),
        }

        # 1. Выполняем HTTP-запрос через весь наш конвейер миддлварей
        # (Используем ручку-заглушку, возвращающую 200 OK)
        response = client.get("/api/v1/public/health_probe_stub", **headers)
        assert response.status_code == 200

        # [ГЛАВНАЯ SRE ПРОВЕРКА]: Блок finally в ContextIsolationMiddleware отработал!
        # ContextVar текущего потока рантайма обязан быть абсолютно пустым (None)
        assert _current_request_context.get() is None
        assert is_primary_db_enforced() is False
