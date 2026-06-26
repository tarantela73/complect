import redis
from django.conf import settings

from apps.common.context.core import get_tenant_id, set_force_primary_db


class ReadYourOwnWritesStickyMiddleware:
    """
    Sticky Read-Your-Own-Writes (rule 4.13): после мутации тенанта
    переключает чтение на primary DB на 5 секунд для предотвращения replica lag.

    Sync-реализация корректна в Django ASGI: async_to_sync при вызове внутренней
    async-цепочки копирует текущий contextvars.Context (включая force_primary=True),
    поэтому мутация ContextVar видна view. Аналогично работают
    ContextIsolationMiddleware и AsgiTenantEnforcementMiddleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.redis_client = redis.from_url(settings.REDIS_CACHE_URL, decode_responses=True)

    def __call__(self, request):
        try:
            tenant_id = get_tenant_id()
            redis_key = f"cqrs:sticky:{tenant_id}"

            if self.redis_client.exists(redis_key):
                set_force_primary_db(True)
        except Exception:
            # Публичные роуты выполняются без CQRS-обвязки
            pass

        response = self.get_response(request)

        # Если запрос был мутирующим и прошел успешно — взводим липкость в Redis на 5 секунд
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and response.status_code < 400:
            try:
                tenant_id = get_tenant_id()
                self.redis_client.setex(f"cqrs:sticky:{tenant_id}", 5, "enforced")
            except Exception:
                pass

        return response
