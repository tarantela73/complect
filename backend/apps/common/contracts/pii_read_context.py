from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Iterator, Literal


class PiiReadContextError(PermissionError):
    """A PII read is rejected when its request context is incomplete."""


@dataclass(frozen=True, slots=True)
class PiiReadContext:
    actor_type: Literal["operator", "system", "user"]
    actor_ref: str
    tenant_id: uuid.UUID
    reason_code: str
    request_id: uuid.UUID
    correlation_id: uuid.UUID

    def __post_init__(self) -> None:
        if self.actor_type not in {"operator", "system", "user"} or not self.actor_ref.strip():
            raise ValueError("PII_ACTOR_REQUIRED")
        if not self.reason_code.strip():
            raise ValueError("PII_REASON_REQUIRED")

    def for_tenant(self, tenant_id: uuid.UUID) -> "PiiReadContext":
        return replace(self, tenant_id=tenant_id)

    def for_reason(self, reason_code: str) -> "PiiReadContext":
        return replace(self, reason_code=reason_code)

    @contextmanager
    def activate(self) -> Iterator[None]:
        token = _current_pii_read_context.set(self)
        try:
            yield
        finally:
            _current_pii_read_context.reset(token)

    @contextmanager
    def activate_for_tenant(self, tenant_id: uuid.UUID) -> Iterator[None]:
        with self.for_tenant(tenant_id).activate():
            yield


_current_pii_read_context: ContextVar[PiiReadContext | None] = ContextVar(
    "pii_read_context", default=None
)


def get_pii_read_context(*, expected_tenant_id: uuid.UUID | None = None) -> PiiReadContext:
    context = _current_pii_read_context.get()
    if context is None:
        raise PiiReadContextError("PII_READ_CONTEXT_REQUIRED")
    if expected_tenant_id is not None and context.tenant_id != expected_tenant_id:
        raise PiiReadContextError("PII_READ_CONTEXT_TENANT_MISMATCH")
    return context


def set_pii_read_context(context: PiiReadContext):
    return _current_pii_read_context.set(context)


def clear_pii_read_context() -> None:
    _current_pii_read_context.set(None)
