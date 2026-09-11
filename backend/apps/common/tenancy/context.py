# backend/apps/common/tenancy/context.py
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings
from django.db import connections, transaction

from apps.common.context.core import (
    ContextCore,
    clear_context,
    get_tenant_id,
    set_tenant_id,
)

_current_execution_context: ContextVar[dict | None] = ContextVar("_current_execution_context", default=None)


def set_tenant_context(tenant_id: uuid.UUID) -> None:
    set_tenant_id(tenant_id)


def clear_tenant_context() -> None:
    clear_context()


@contextmanager
def scoped_tenant_context(tenant_id: uuid.UUID) -> Iterator[None]:
    """Temporarily replace only tenant context and restore every outer field."""
    token = ContextCore.tenant_id.set(tenant_id)
    try:
        yield
    finally:
        ContextCore.tenant_id.reset(token)


@contextmanager
def tenant_rls_scope(tenant_id: uuid.UUID, *, using: str = "default") -> Iterator[None]:
    """Keep the tenant ContextVar and PostgreSQL transaction GUC in sync."""
    connection = connections[using]
    if connection.vendor != "postgresql":
        raise RuntimeError("TENANT_RLS_REQUIRES_POSTGRESQL")
    namespace = settings.RLS_NAMESPACE
    with transaction.atomic(using=using):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting(%s, true)", [namespace])
            previous = cursor.fetchone()[0] or ""
            cursor.execute("SELECT set_config(%s, %s, true)", [namespace, str(tenant_id)])
        token = ContextCore.tenant_id.set(tenant_id)
        try:
            yield
        except BaseException:
            ContextCore.tenant_id.reset(token)
            raise
        else:
            try:
                if not connection.needs_rollback:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT set_config(%s, %s, true)", [namespace, previous])
            finally:
                ContextCore.tenant_id.reset(token)


def get_current_tenant_id() -> uuid.UUID:
    return get_tenant_id()


def get_sre_context() -> dict:
    return _current_execution_context.get() or {}
