import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import jwt
from django.conf import settings
from django.http import HttpRequest
from ninja.security import HttpBearer

from apps.common.context.core import ContextCore, set_correlation_id, set_request_id, set_tenant_id, set_user_id
from apps.common.contracts.auth_context import ValidatedAuthContext, get_auth_context_validator


@dataclass(frozen=True, slots=True)
class _VerifiedRequestAuthentication:
    context: ValidatedAuthContext
    token: str = field(repr=False)
    payload: dict[str, Any] = field(repr=False)


def verified_request_context(request) -> ValidatedAuthContext | None:
    """Reject drift rather than letting a later auth/RBAC writer repair it."""
    verified = getattr(request, "_verified_authentication", None)
    if not isinstance(verified, _VerifiedRequestAuthentication):
        return None
    context = verified.context
    if (
        getattr(request, "jwt_user_id", None) != context.actor_id
        or getattr(request, "jwt_tenant_id", None) != context.tenant_id
        or ContextCore.user_id.get() != context.actor_id
        or ContextCore.tenant_id.get() != context.tenant_id
    ):
        return None
    return context


class GatewayHttpRequest(HttpRequest):
    """HTTP request projection populated by the verified gateway middleware."""

    gateway_client_ip: str


class AuthenticatedHttpRequest(GatewayHttpRequest):
    """HTTP request projection populated by the verified gateway authentication."""

    jwt_user_id: uuid.UUID
    jwt_tenant_id: uuid.UUID


class JWTAuth(HttpBearer):
    def authenticate(self, request, token: str) -> Optional[dict[str, Any]]:
        verified = getattr(request, "_verified_authentication", None)
        if verified is not None:
            if (
                isinstance(verified, _VerifiedRequestAuthentication)
                and verified.token == token
                and verified_request_context(request) is not None
            ):
                return verified.payload
            return None
        # HTTP authentication cannot adopt an ambient/internal propagation context.
        if ContextCore.tenant_id.get() is not None or ContextCore.user_id.get() is not None:
            return None
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"], options={"require": ["exp"]})
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None
        if payload.get("type") != "access":
            return None

        validator = get_auth_context_validator()
        if validator is None:
            return None
        try:
            context = validator.validate(payload)
        except Exception:
            return None

        # Parse non-authoritative tracing metadata before publishing any identity.
        request_id = uuid.UUID(request.headers.get("X-Request-ID") or str(uuid.uuid4()))
        correlation_id = uuid.UUID(request.headers.get("X-Correlation-ID") or str(uuid.uuid4()))
        if context.actor_id.version != 4 or context.tenant_id.version != 4:
            return None
        set_user_id(context.actor_id)
        set_tenant_id(context.tenant_id)
        set_request_id(request_id)
        set_correlation_id(correlation_id)
        request.jwt_user_id = context.actor_id
        request.jwt_tenant_id = context.tenant_id
        request._verified_authentication = _VerifiedRequestAuthentication(context, token, payload)
        return payload


jwt_auth = JWTAuth()
