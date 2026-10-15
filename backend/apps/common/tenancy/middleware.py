# backend/apps/common/tenancy/middleware.py
import uuid
from secrets import compare_digest

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import JsonResponse

from apps.common.api.auth import jwt_auth
from apps.common.api.errors import make_error_response
from apps.common.context.core import set_tenant_id
from apps.common.context.guard import ContextGuard

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

    # Exact allowlist: protected MFA/convergence routes must authenticate here too.
    CONTEXT_FREE_AUTH_PATHS = (
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/mfa/verify",
    )
    CONTEXT_FREE_PATHS = (
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
            raise ImproperlyConfigured("PUBLIC_SITE_TENANT_ID must be set in settings when apps.site is installed.")
        try:
            uuid.UUID(str(raw))
        except (ValueError, AttributeError) as exc:
            raise ImproperlyConfigured(f"PUBLIC_SITE_TENANT_ID is not a valid UUID: {raw!r}") from exc

    def __call__(self, request):
        path = request.path_info
        try:
            ContextGuard.assert_clean()

            if request.method == "OPTIONS" or path.startswith(self.PUBLIC_PREFIXES):
                return self.get_response(request)

            # Public API: anonymous access with tenant injected from config (ADR-0023)
            if path.startswith(PUBLIC_API_PREFIX):
                tenant_id = uuid.UUID(str(settings.PUBLIC_SITE_TENANT_ID))
                set_tenant_id(tenant_id)
                return self.get_response(request)

            # GATEWAY TRUST BOUNDARY — enforced for all non-public paths
            expected_token = getattr(settings, "EXPECTED_GATEWAY_TOKEN", None)
            if expected_token:
                gateway_token = request.headers.get("X-Gateway-Token", "")
                if not compare_digest(gateway_token, expected_token):
                    return JsonResponse(
                        make_error_response("GATEWAY_TRUST_VIOLATION", "Недействительный шлюзовой токен."),
                        status=403,
                    )

            # Auth and schema endpoints: gateway-trusted but context-free
            if (request.method == "POST" and path in self.CONTEXT_FREE_AUTH_PATHS) or (
                request.method in {"GET", "HEAD"} and path.rstrip("/") in self.CONTEXT_FREE_PATHS
            ):
                return self.get_response(request)

            if not jwt_auth(request):
                return JsonResponse(
                    make_error_response(
                        "AUTH_GATE_DENIED",
                        "Требуется действительный access JWT.",
                    ),
                    status=401,
                )

            ContextGuard.validate_consistency()
            return self.get_response(request)

        except (ValueError, TypeError) as e:
            return JsonResponse(
                make_error_response("MALFORMED_CONTEXT_HEADERS", str(e)),
                status=400,
            )

        finally:
            for attribute in ("_verified_authentication", "jwt_user_id", "jwt_tenant_id"):
                request.__dict__.pop(attribute, None)
            ContextGuard.hard_reset()
