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
_auth_rollout_mode: Any = None


def register_auth_context_validator(validator: AuthContextValidationProtocol) -> None:
    global _validator
    if _validator is not None and _validator is not validator:
        raise RuntimeError("Auth context validator is already registered")
    _validator = validator


def get_auth_context_validator() -> AuthContextValidationProtocol | None:
    return _validator


def register_auth_rollout_mode(provider) -> None:
    global _auth_rollout_mode
    if _auth_rollout_mode is not None and _auth_rollout_mode is not provider:
        raise RuntimeError("Auth rollout mode already registered")
    _auth_rollout_mode = provider


def get_auth_rollout_mode():
    if _auth_rollout_mode is None:
        raise RuntimeError("AUTH_ROLLOUT_PROVIDER_UNAVAILABLE")
    return _auth_rollout_mode()
