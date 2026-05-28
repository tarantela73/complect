import uuid
from contextvars import ContextVar

from django.core.exceptions import PermissionDenied
from django.template import RequestContext


class ContextCore:
    """
    [ФИНАЛЬНЫЙ КАНОН] Изолированное персистентное хранилище метаданных потока выполнения.
    Категорически запрещено импортировать этот класс напрямую в доменные слои (модели/сервисы).
    """

    tenant_id: ContextVar[uuid.UUID | None] = ContextVar("tenant_id", default=None)
    user_id: ContextVar[uuid.UUID | None] = ContextVar("user_id", default=None)
    request_id: ContextVar[uuid.UUID | None] = ContextVar("request_id", default=None)
    correlation_id: ContextVar[uuid.UUID | None] = ContextVar("correlation_id", default=None)
    force_primary: ContextVar[bool | None] = ContextVar("force_primary", default=False)


# ==========================================
# SAFE ACCESSORS (Чистые Геттеры Системы)
# ==========================================


def get_tenant_id() -> uuid.UUID:
    value = ContextCore.tenant_id.get()
    if value is None:
        raise PermissionDenied("CRITICAL_SECURITY_VIOLATION: tenant missing in context")
    return value


def get_user_id() -> uuid.UUID:
    value = ContextCore.user_id.get()
    if value is None:
        raise PermissionDenied("CRITICAL_SECURITY_VIOLATION: user missing in context")
    return value


def get_request_id() -> uuid.UUID:
    value = ContextCore.request_id.get()
    if value is None:
        raise PermissionDenied("SRE_VIOLATION: request_id missing in context")
    return value


def get_correlation_id() -> uuid.UUID:
    value = ContextCore.correlation_id.get()
    if value is None:
        raise PermissionDenied("SRE_VIOLATION: correlation_id missing in context")
    return value


def is_primary_db_enforced() -> bool:
    # Фолбэчимся в Ложь, если флаг пустой
    return bool(ContextCore.force_primary.get())


# ==========================================
# HARD MUTATORS (Управление состоянием)
# ==========================================


def set_tenant_id(value: uuid.UUID) -> None:
    ContextCore.tenant_id.set(value)


def set_user_id(value: uuid.UUID) -> None:
    ContextCore.user_id.set(value)


def set_request_id(value: uuid.UUID) -> None:
    ContextCore.request_id.set(value)


def set_correlation_id(value: uuid.UUID) -> None:
    ContextCore.correlation_id.set(value)


def set_force_primary_db(value: bool) -> None:
    ContextCore.force_primary.set(value)


def set_context(ctx: "RequestContext") -> None:
    ContextCore.tenant_id.set(ctx.tenant_id)
    ContextCore.user_id.set(ctx.user_id)
    ContextCore.request_id.set(ctx.request_id)
    ContextCore.correlation_id.set(ctx.correlation_id)


def clear_context() -> None:
    """
    [ИСПРАВЛЕНО] Потоковый SRE-утилизатор памяти.
    Последовательно сбрасывает все ContextVar токены в исходный дефолтный стейт.
    """
    ContextCore.tenant_id.set(None)
    ContextCore.user_id.set(None)
    ContextCore.request_id.set(None)
    ContextCore.correlation_id.set(None)
    ContextCore.force_primary.set(False)
