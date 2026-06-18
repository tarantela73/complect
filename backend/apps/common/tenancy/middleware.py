# backend/apps/common/tenancy/middleware.py
import uuid

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import JsonResponse

from apps.common.api.errors import make_error_response
from apps.common.context.core import set_tenant_id
from apps.common.context.guard import ContextGuard
from apps.common.context.propagation import apply_propagation_context

# Prefix for anonymous public endpoints (Cloudflare Turnstile + tenant-from-config).
PUBLIC_API_PREFIX = "/api/v1/public/"


class AsgiTenantEnforcementMiddleware:
    # No gateway token, no context headers required.
    PUBLIC_PREFIXES = (
        "/public/",
        "/static/",
        "/metrics/",
        "/health/",
        "/favicon.ico",
        "/sitemap.xml",
        "/robots.txt",
    )

    # Gateway token required, but no tenant/user context headers.
    # Used by auth endpoints that establish identity from scratch.
    CONTEXT_FREE_PREFIXES = (
        "/api/v1/auth/",
        "/api/v1/docs",
        "/api/v1/openapi.json",
        "/api/v1/openapi-json",
    )

    def __init__(self, get_response):
        self.get_response = get_response
        self._validate_public_site_settings()

    @staticmethod
    def _validate_public_site_settings() -> None:
        raw = getattr(settings, "PUBLIC_SITE_TENANT_ID", None)
        if raw is None:
            raise ImproperlyConfigured(
                "PUBLIC_SITE_TENANT_ID must be set in settings when apps.site is installed."
            )
        try:
            uuid.UUID(str(raw))
        except (ValueError, AttributeError) as exc:
            raise ImproperlyConfigured(
                f"PUBLIC_SITE_TENANT_ID is not a valid UUID: {raw!r}"
            ) from exc

    def __call__(self, request):
        if request.method == "OPTIONS":
            return self.get_response(request)

        path = request.path_info

        if path.startswith(self.PUBLIC_PREFIXES):
            return self.get_response(request)

        try:
            ContextGuard.assert_clean()

            # Public API: anonymous access with tenant injected from config (ADR-0023)
            if path.startswith(PUBLIC_API_PREFIX):
                tenant_id = uuid.UUID(str(settings.PUBLIC_SITE_TENANT_ID))
                set_tenant_id(tenant_id)
                return self.get_response(request)

            # GATEWAY TRUST BOUNDARY — enforced for all non-public paths
            expected_token = getattr(settings, "EXPECTED_GATEWAY_TOKEN", None)
            if expected_token:
                gateway_token = request.headers.get("X-Gateway-Token")
                if gateway_token != expected_token:
                    return JsonResponse(
                        make_error_response("GATEWAY_TRUST_VIOLATION", "Недействительный шлюзовой токен."),
                        status=403,
                    )

            # Auth and schema endpoints: gateway-trusted but context-free
            if path.startswith(self.CONTEXT_FREE_PREFIXES):
                return self.get_response(request)

            success, user_uuid, tenant_uuid = apply_propagation_context(request.headers)

            if not success:
                return JsonResponse(
                    make_error_response(
                        "AUTH_GATE_DENIED",
                        "Требуются действительные заголовки X-Tenant-ID / X-User-ID.",
                    ),
                    status=401,
                )

            # Make identity available to require_permission regardless of auth path.
            # JWT Bearer sets these in JWTAuth.authenticate(); gateway sets them here.
            request.jwt_user_id = user_uuid
            request.jwt_tenant_id = tenant_uuid

            ContextGuard.validate_consistency()
            return self.get_response(request)

        except (ValueError, TypeError) as e:
            return JsonResponse(
                make_error_response("MALFORMED_CONTEXT_HEADERS", str(e)),
                status=400,
            )

        finally:
            ContextGuard.hard_reset()
