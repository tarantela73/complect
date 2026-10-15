import uuid
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ValidatedAuthContext:
    actor_id: uuid.UUID
    tenant_id: uuid.UUID


class AuthContextValidationProtocol(Protocol):
    def validate(self, claims: dict[str, Any]) -> ValidatedAuthContext: ...


_validator: AuthContextValidationProtocol | None = None


def register_auth_context_validator(validator: AuthContextValidationProtocol) -> None:
    global _validator
    if _validator is not None and _validator is not validator:
        raise RuntimeError("Auth context validator is already registered")
    _validator = validator


def get_auth_context_validator() -> AuthContextValidationProtocol | None:
    return _validator
