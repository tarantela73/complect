# backend/apps/common/tenancy/context_propagation.py
import uuid

from apps.common.tenancy.context_core import RequestContext


class ContextPropagation:
    @staticmethod
    def to_headers(ctx: RequestContext) -> dict:
        return {
            "x-ctx-tenant-id": str(ctx.tenant_id),
            "x-ctx-user-id": str(ctx.user_id),
            "x-ctx-request-id": str(ctx.request_id),
            "x-ctx-correlation-id": str(ctx.correlation_id),
        }

    @staticmethod
    def from_headers(headers: dict) -> RequestContext | None:
        tenant_id = headers.get("x-ctx-tenant-id") or headers.get("X-Tenant-ID")
        user_id = headers.get("x-ctx-user-id") or headers.get("X-User-ID")
        request_id = headers.get("x-ctx-request-id") or headers.get("X-Request-ID")
        correlation_id = headers.get("x-ctx-correlation-id") or headers.get("X-Correlation-ID")

        if not tenant_id or not user_id:
            return None

        try:
            return RequestContext(
                tenant_id=uuid.UUID(str(tenant_id)),
                user_id=uuid.UUID(str(user_id)),
                request_id=uuid.UUID(str(request_id)) if request_id else uuid.uuid4(),
                correlation_id=uuid.UUID(str(correlation_id)) if correlation_id else uuid.uuid4(),
            )
        except (ValueError, TypeError):
            return None
