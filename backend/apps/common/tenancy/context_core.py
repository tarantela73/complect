import uuid
from dataclasses import dataclass

from apps.common.context.core import (
    get_correlation_id,
    get_request_id,
    get_tenant_id,
    get_user_id,
)
from apps.common.context.core import (
    set_context as set_context,  # re-export для tenancy.context_core
)


@dataclass
class RequestContext:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    request_id: uuid.UUID
    correlation_id: uuid.UUID


def get_context() -> RequestContext:
    return RequestContext(
        tenant_id=get_tenant_id(),
        user_id=get_user_id(),
        request_id=get_request_id(),
        correlation_id=get_correlation_id(),
    )
