from apps.common.context.guard import ContextGuard
from apps.common.contracts.pii_read_context import clear_pii_read_context


class ContextIsolationMiddleware:
    """
    [ФИНАЛЬНЫЙ PRODUCTION КАНОН v3.7.4]
    Нулевой слой изоляции. Гарантирует абсолютную идемпотентность и leak-safe
    состояние потока за счет принудительного сброса ContextVar до и после запроса.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 1. PRE-CLEANUP: Полностью обнуляем стейт перед обработкой (защита от утечек тест-раннера)
        ContextGuard.hard_reset()

        try:
            response = self.get_response(request)
            return response
        finally:
            # 2. POST-CLEANUP: Намертво выжигаем ContextVar из потока при любом исходе запроса
            ContextGuard.hard_reset()
            clear_pii_read_context()

            ContextGuard.hard_reset()
