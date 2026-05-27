import uuid
from typing import Dict, Any
from apps.common.context.core import set_tenant_id, set_user_id, set_request_id, set_correlation_id


def apply_propagation_context(raw_headers: Dict[str, Any]) -> bool:
    """
    [ФИНАЛЬНЫЙ КАНОН] Универсальный транзитный демаршалер контекста платформы Комплект.
    [ИСПРАВЛЕНО]: Исправлена синтаксическая ошибка синтаксиса логических выражений walrus.
    Вычитывает метаданные из транспортных заголовков, жестко валидирует типы и взаимно наполняет ContextCore.
    """
    # 1. Распознаем тип источника. Если заголовки пришли от Celery, у них префикс x-ctx-
    tenant_id = raw_headers.get("x-ctx-tenant-id") or raw_headers.get("X-Tenant-ID")
    user_id = raw_headers.get("x-ctx-user-id") or raw_headers.get("X-User-ID")
    request_id = raw_headers.get("x-ctx-request-id") or raw_headers.get("X-Request-ID")
    correlation_id = raw_headers.get("x-ctx-correlation-id") or raw_headers.get("X-Correlation-ID")

    if not tenant_id or not user_id:
        return False

    try:
        # Приводим к строковому формату для гарантированного текстового матчинга
        tenant_str = str(tenant_id).strip()
        user_str = str(user_id).strip()

        # 2. Hardening валидация: Мгновенное отсечение опасных атак Nil-UUID [Пункт 2]
        nil_uuid = "00000000-0000-0000-0000-000000000000"
        if tenant_str == nil_uuid or user_str == nil_uuid:
            return False

        t_uuid = uuid.UUID(tenant_str)
        u_uuid = uuid.UUID(user_str)

        # Гарантируем, что в систему залетают только легитимные UUID v4
        if t_uuid.version != 4 or u_uuid.version != 4:
            return False

        # 3. Атомарное наполнение плоского ядра ContextCore
        set_tenant_id(t_uuid)
        set_user_id(u_uuid)
        set_request_id(uuid.UUID(str(request_id)) if request_id else uuid.uuid4())
        set_correlation_id(uuid.UUID(str(correlation_id)) if correlation_id else uuid.uuid4())
        return True

    except (ValueError, TypeError, AttributeError):
        return False
