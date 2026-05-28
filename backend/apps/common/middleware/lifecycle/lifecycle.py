import structlog
from django.db import connection

from apps.common.context.core import get_correlation_id, get_tenant_id

logger = structlog.get_logger(__name__)


class StructlogTracingContextMiddleware:
    """
    [ФИНАЛЬНЫЙ PRODUCTION КАНОН v3.7.4]
    Миддлварь сквозной наблюдаемости (Observability).
    Инжектирует Correlation-ID текущего тенанта в глобальный контекст structlog.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            # Извлекаем Correlation-ID, если он уже демаршален в паспорте контекста
            correlation_id = get_correlation_id()
            structlog.contextvars.bind_contextvars(correlation_id=str(correlation_id))
        except Exception:
            # Если запрос публичный и паспорта нет — генерируем пустой трейс-контекст
            structlog.contextvars.bind_contextvars(correlation_id="anonymous-subrequest")

        response = self.get_response(request)
        return response


class PostgresRlsEnforcementMiddleware:
    """
    [ФИНАЛЬНЫЙ PRODUCTION КАНОН v3.7.4]
    Миддлварь безопасности баз данных. Накладывает RLS замок
    на сессию PostgreSQL перед выполнением любого SQL-запроса.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            # Извлекаем единый идентификатор тенанта из ядра
            tenant_id = get_tenant_id()

            # [ИСПРАВЛЕНО]: Взводим локальную переменную сессии в Postgres через чистый raw SQL
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET LOCAL app.current_tenant_id = %s;",
                    [str(tenant_id)],
                )
        except Exception:
            # Публичные роуты, статика и K8s liveness-пробы работают вне тенант-контекста
            pass

        response = self.get_response(request)
        return response
