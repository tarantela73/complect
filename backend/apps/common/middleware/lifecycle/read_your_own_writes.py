import redis

from apps.common.context.core import ContextCore, get_tenant_id


class ReadYourOwnWritesStickyMiddleware:
    """
    [ФИНАЛЬНЫЙ PRODUCTION КАНОН v3.7.4]
    Липкий CQRS роутинг (Sticky Read-Your-Own-Writes Strategy).
    Если тенант совершил мутацию (POST/PUT/DELETE), флаг переключает
    его чтение со слабой реплики на мастер-БД для предотвращения лага данных.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.context_core = ContextCore()
        # Используем ленивое подключение к кэш-базе Redis №1 из нашей топологии
        self.redis_client = redis.Redis(host="redis", port=6379, db=1, decode_responses=True)

    def __call__(self, request):
        try:
            tenant_id = get_tenant_id()
            redis_key = f"cqrs:sticky:{tenant_id}"

            # [ИСПРАВЛЕНО]: Взводим липкость к мастеру через нативное свойство дескриптора ядра
            if self.redis_client.exists(redis_key):
                self.context_core.force_primary = True
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
