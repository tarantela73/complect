import uuid
from typing import Dict, Any, Optional

from apps.common.tenancy.context_core import (
    RequestContext,
    set_context,
    get_context,
)


class ContextPropagation:
    """
    Единый слой сериализации/десериализации RequestContext
    для Celery / Channels / async jobs.
    """

    HEADER_PREFIX = "x-ctx-"

    # =========================
    # SERIALIZATION
    # =========================
    @staticmethod
    def to_headers(ctx: RequestContext) -> Dict[str, str]:
        return {
            f"{ContextPropagation.HEADER_PREFIX}tenant-id": str(ctx.tenant_id),
            f"{ContextPropagation.HEADER_PREFIX}user-id": str(ctx.user_id),
            f"{ContextPropagation.HEADER_PREFIX}request-id": str(ctx.request_id),
            f"{ContextPropagation.HEADER_PREFIX}correlation-id": str(ctx.correlation_id),
        }

    @staticmethod
    def from_headers(headers: Dict[str, str]) -> Optional[RequestContext]:
        try:
            tenant_id = headers.get("x-ctx-tenant-id")
            user_id = headers.get("x-ctx-user-id")
            request_id = headers.get("x-ctx-request-id")
            correlation_id = headers.get("x-ctx-correlation-id")

            if not tenant_id or not user_id:
                return None

            return RequestContext(
                tenant_id=uuid.UUID(tenant_id),
                user_id=uuid.UUID(user_id),
                request_id=uuid.UUID(request_id) if request_id else uuid.uuid4(),
                correlation_id=uuid.UUID(correlation_id) if correlation_id else uuid.uuid4(),
            )
        except Exception:
            return None

    # =========================
    # APPLY CONTEXT
    # =========================
    @staticmethod
    def activate(ctx: Optional[RequestContext]) -> None:
        if ctx:
            set_context(ctx)
