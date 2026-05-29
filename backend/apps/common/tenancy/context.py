# backend/apps/common/tenancy/context.py
import uuid
from contextvars import ContextVar

from apps.common.context.core import (
    clear_context,
    get_tenant_id,
    set_tenant_id,
)

_current_execution_context: ContextVar[dict] = ContextVar("_current_execution_context", default={})


def set_tenant_context(tenant_id: uuid.UUID) -> None:
    set_tenant_id(tenant_id)


def clear_tenant_context() -> None:
    clear_context()


def get_current_tenant_id() -> uuid.UUID:
    return get_tenant_id()


def get_sre_context() -> dict:
    return _current_execution_context.get()
