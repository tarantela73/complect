import structlog
from apps.common.tenancy.context_core import get_context

logger = structlog.get_logger(__name__)


class StructlogTracingContextMiddleware:
    """
    [ADR 0004 / СИНХРОНИЗАЦИЯ] Сборщик контекста распределенного трейсинга OpenTelemetry.
    Намертво связывает structlog JSON-процессоры с твоим единым RequestContext.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            # Извлекаем единый объект контекста
            ctx = get_context()

            # Принудительно транслируем UUID-метки в контекст процессоров structlog
            structlog.contextvars.clear_contextvars()
            structlog.contextvars.bind_contextvars(
                request_id=str(ctx.request_id),
                correlation_id=str(ctx.correlation_id),
                tenant_id=str(ctx.tenant_id),
                user_id=str(ctx.user_id),
                module=request.resolver_match.app_name if request.resolver_match else "core",
            )
        except Exception:
            # Фолбэк для анонимных публичных запросов
            structlog.contextvars.clear_contextvars()

        response = self.get_response(request)

        try:
            # Инжектируем correlation_id обратно в HTTP-ответ для Zustand фронтенда
            response["X-Correlation-ID"] = str(get_context().correlation_id)
        except Exception:
            pass

        return response
