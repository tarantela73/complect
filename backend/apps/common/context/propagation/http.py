# backend/apps/common/context/propagation/http.py
from typing import Any, Dict, Optional


def extract_http_context(headers: Any) -> Dict[str, Optional[str]]:
    """
    [ФИНАЛЬНЫЙ КАНОН] Извлекает сырые строковые метаданные из HTTP-заголовков.
    Работает одинаково надежно с ASGI request.headers и WSGI-словарями.
    """
    # Поддерживаем как mapping-объекты заголовков Django, так и плоские словари
    get_header = getattr(headers, "get", lambda k: headers.get(k))

    return {
        "tenant_id": get_header("x-tenant-id") or get_header("X-Tenant-ID"),
        "user_id": get_header("x-user-id") or get_header("X-User-ID"),
        "request_id": get_header("x-request-id") or get_header("X-Request-ID"),
        "correlation_id": get_header("x-correlation-id") or get_header("X-Correlation-ID"),
    }
