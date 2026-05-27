from typing import Dict
from apps.common.context.core import ContextCore


def inject_celery_headers(headers: dict) -> Dict[str, str]:
    """
    [ФИНАЛЬНЫЙ КАНОН] Снимает байтовый слепок текущего контекста потока
    и инжектирует его в AMQP/Redis заголовки Celery-задачи перед отправкой.
    """
    t_id = ContextCore.tenant_id.get()
    u_id = ContextCore.user_id.get()
    r_id = ContextCore.request_id.get()
    c_id = ContextCore.correlation_id.get()

    # Если контекст отсутствует (например, системный крон Celery Beat), возвращаем исходные заголовки
    if not t_id or not u_id:
        return headers

    return {
        **headers,
        "x-ctx-tenant-id": str(t_id),
        "x-ctx-user-id": str(u_id),
        "x-ctx-request-id": str(r_id),
        "x-ctx-correlation-id": str(c_id),
    }
