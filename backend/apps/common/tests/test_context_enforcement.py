import pytest
from apps.common.context.core import set_tenant_id, clear_context
from apps.common.context.guard import ContextGuard


# Эмулируем защищенную функцию доменного слоя
@ContextGuard.require_tenant
def secure_billing_operation_stub():
    return "SUCCESS_MUTATION"


class TestContextGuardEnforcement:
    def test_require_tenant_decorator_blocks_execution_without_context(self):
        """[LEVEL 8: SECURITY] Проверяет выброс RuntimeError при попытке вызвать код без тенанта."""
        # 1. Гарантируем, что контекст пуст
        clear_context()

        # 2. Попытка вызвать защищенную операцию обязана намертво упасть!
        with pytest.raises(RuntimeError, match="TENANT_CONTEXT_REQUIRED"):
            secure_billing_operation_stub()

    def test_require_tenant_decorator_allows_execution_with_context(self):
        """[LEVEL 8: SECURITY] Проверяет успешный пропуск вызова при легитимном контексте."""
        import uuid

        # 1. Задаем контекст
        set_tenant_id(uuid.uuid4())

        # 2. Вызов проходит без ошибок
        result = secure_billing_operation_stub()
        assert result == "SUCCESS_MUTATION"

        # Чистим за собой
        clear_context()
