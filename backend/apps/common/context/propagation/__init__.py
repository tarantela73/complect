# backend/apps/common/context/propagation/__init__.py
import uuid
from typing import Any, Dict

from apps.common.context.core import set_correlation_id, set_request_id, set_tenant_id, set_user_id


def apply_propagation_context(
    raw_headers: Dict[str, Any],
) -> tuple[bool, uuid.UUID | None, uuid.UUID | None]:
    """Return (success, user_uuid, tenant_uuid). On failure returns (False, None, None)."""
    tenant_id = raw_headers.get("x-ctx-tenant-id") or raw_headers.get("X-Tenant-ID")
    user_id = raw_headers.get("x-ctx-user-id") or raw_headers.get("X-User-ID")
    request_id = raw_headers.get("x-ctx-request-id") or raw_headers.get("X-Request-ID")
    correlation_id = raw_headers.get("x-ctx-correlation-id") or raw_headers.get("X-Correlation-ID")

    # Отсутствие обязательных заголовков → 401
    if not tenant_id or not user_id:
        return False, None, None

    tenant_str = str(tenant_id).strip()
    user_str = str(user_id).strip()

    nil_uuid = "00000000-0000-0000-0000-000000000000"
    if tenant_str == nil_uuid or user_str == nil_uuid:
        raise ValueError("Nil UUID forbidden")

    # ValueError намеренно НЕ перехватывается — пробрасывается в middleware → 400
    t_uuid = uuid.UUID(tenant_str)
    u_uuid = uuid.UUID(user_str)

    if t_uuid.version != 4 or u_uuid.version != 4:
        raise ValueError(f"Non-v4 UUID rejected: tenant={t_uuid.version}, user={u_uuid.version}")

    r_uuid = uuid.UUID(str(request_id).strip()) if request_id else uuid.uuid4()

    # correlation_id тоже валидируем строго — пробрасываем ValueError если битый
    if correlation_id:
        c_uuid = uuid.UUID(str(correlation_id).strip())
    else:
        c_uuid = uuid.uuid4()

    set_tenant_id(t_uuid)
    set_user_id(u_uuid)
    set_request_id(r_uuid)
    set_correlation_id(c_uuid)
    return True, u_uuid, t_uuid
