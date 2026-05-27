import structlog
from apps.common.tenancy.context_guard import ContextGuard

logger = structlog.get_logger(__name__)


class ContextIsolationMiddleware:
    """
    [ADR 0021 / ИСПРАВЛЕНО] Нулевой уровень тотальной изоляции ASGI-монолита.
    Очищает оперативную память потока до и после обработки, ликвидируя Phantom Auth Context.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Превентивное выжигание остаточного состояния потока до старта проверки
        ContextGuard.hard_reset()

        try:
            return self.get_response(request)
        except Exception as exc:
            logger.error("CONTEXT_ISOLATION_PIPELINE_CRASH", error=str(exc))
            raise exc
        finally:
            # Гарантированная очистка памяти при выходе HTTP-пакета из системы
            ContextGuard.hard_reset()
