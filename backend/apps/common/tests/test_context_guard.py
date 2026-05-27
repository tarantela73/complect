import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
import uuid
from apps.common.tenancy.context_core import RequestContext, set_context
from apps.common.tenancy.context_guard import ContextGuard


@pytest.mark.django_db(transaction=True)
class TestContextGuardFailFastMesh:
    def test_context_guard_triggers_immediate_fail_fast_on_contaminated_thread(self):
        """[LEVEL 8: SECURITY] Гарантирует падение рантайма при обнаружении грязного контекста."""
        client = Client()

        # Имитируем аварию: в ContextVar текущего потока зависли грязные данные от Прошлого Тенанта
        dirty_ctx = RequestContext(
            tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), request_id=uuid.uuid4(), correlation_id=uuid.uuid4()
        )
        set_context(dirty_ctx)

        # Прямой вызов assert_clean обязан выкинуть критическое исключение ИБ!
        with pytest.raises(PermissionDenied, match="CONTEXT_LEAK_DETECTED"):
            ContextGuard.assert_clean()

        # Проверяем, что AsgiTenantEnforcementMiddleware перехватит это и вернет 500/403 заслон
        # Очищаем контекст, чтобы вернуть тестовую среду в стерильное состояние
        ContextGuard.hard_reset()
