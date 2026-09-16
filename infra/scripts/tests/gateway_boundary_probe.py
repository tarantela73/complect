"""Test-only ASGI transport probe: real JWTAuth, fixed users Protocol double."""

# Django settings must be configured before importing the application boundary.
# ruff: noqa: E402

import io
import uuid

from django.conf import settings

settings.configure(
    SECRET_KEY="gateway-runtime-test-signing-key-32-bytes",
    DEBUG=False,
    ALLOWED_HOSTS=["*"],
    INSTALLED_APPS=[],
    EXPECTED_GATEWAY_TOKEN="verification-only-gateway",
    PUBLIC_SITE_TENANT_ID="00000000-0000-4000-8000-000000000002",
)
import django

django.setup()
from django.core.exceptions import PermissionDenied
from django.core.handlers.asgi import ASGIRequest
from django.http import JsonResponse

from apps.common.context.core import ContextCore
from apps.common.contracts.auth_context import ValidatedAuthContext, register_auth_context_validator
from apps.common.tenancy.middleware import AsgiTenantEnforcementMiddleware

ACTOR = uuid.UUID("00000000-0000-4000-8000-000000000001")
TENANT = uuid.UUID("00000000-0000-4000-8000-000000000002")


class Validator:
    def validate(self, claims):
        if claims.get("user_id") != str(ACTOR) or claims.get("tenant_id") != str(TENANT):
            raise PermissionDenied("INVALID_AUTH_CONTEXT")
        return ValidatedAuthContext(ACTOR, TENANT)


register_auth_context_validator(Validator())


def endpoint(request):
    return JsonResponse(
        {
            "sanitized": not any(
                name in request.headers for name in ("X-User-ID", "X-Tenant-ID", "X-CTX-User-ID", "X-CTX-Tenant-ID")
            ),
            "tenant": str(ContextCore.tenant_id.get()) if ContextCore.tenant_id.get() else None,
            "actor": str(ContextCore.user_id.get()) if ContextCore.user_id.get() else None,
        }
    )


middleware = AsgiTenantEnforcementMiddleware(endpoint)


async def application(scope, receive, send):
    if scope["type"] != "http":
        return
    request = ASGIRequest(scope, io.BytesIO())
    response = middleware(request)
    await send(
        {
            "type": "http.response.start",
            "status": response.status_code,
            "headers": [(key.lower().encode(), value.encode()) for key, value in response.items()],
        }
    )
    await send({"type": "http.response.body", "body": response.content})
