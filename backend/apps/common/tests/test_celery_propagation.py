import uuid
from unittest.mock import MagicMock

import pytest

from apps.common.tenancy.context_core import RequestContext, get_context, set_context
from apps.common.tenancy.context_propagation import ContextPropagation


@pytest.mark.django_db
class TestCeleryTaskContextPropagation:
    def test_apply_async_correctly_embeds_x_ctx_headers(self):
        """[LEVEL 1: UNIT] Гарантирует правильный маршалинг контекста при отправке задачи через apply_async."""
        # 1. Формируем эталонный паспорт контекста текущего HTTP-потока
        source_ctx = RequestContext(
            tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), request_id=uuid.uuid4(), correlation_id=uuid.uuid4()
        )
        set_context(source_ctx)

        # Извлекаем контекст через ядро
        ctx = get_context()

        # 2. Имитируем вызов отправки Celery таска строго по новому Enterprise-стандарту [Пункт 4]
        # Собираем x-ctx-* заголовки через фабричный метод
        task_headers = ContextPropagation.to_headers(ctx)

        assert task_headers["x-ctx-tenant-id"] == str(source_ctx.tenant_id)
        assert task_headers["x-ctx-request-id"] == str(source_ctx.request_id)

        # 3. Эмулируем прохождение через ContextTask воркер (Демаршалинг на стороне Celery)
        mock_task = MagicMock()
        mock_task.request.headers = task_headers

        # Вызываем логику восстановления контекста из кастомного ContextTask
        restored_ctx = ContextPropagation.from_headers(mock_task.request.headers)

        assert restored_ctx is not None
        assert restored_ctx.tenant_id == source_ctx.tenant_id
        assert restored_ctx.correlation_id == source_ctx.correlation_id
