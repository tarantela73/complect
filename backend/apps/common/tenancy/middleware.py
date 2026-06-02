# backend/apps/common/tenancy/middleware.py
from django.conf import settings
from django.http import JsonResponse

from apps.common.api.errors import make_error_response
from apps.common.context.guard import ContextGuard
from apps.common.context.propagation import apply_propagation_context


class AsgiTenantEnforcementMiddleware:
    # No gateway token, no context headers required.
    PUBLIC_PREFIXES = (
        "/public/",
        "/static/",
        "/metrics/",
        "/health/",
        "/favicon.ico",
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

    def __call__(self, request):
        if request.method == "OPTIONS":
            return self.get_response(request)

        path = request.path_info

        if path.startswith(self.PUBLIC_PREFIXES):
            return self.get_response(request)

        try:
            ContextGuard.assert_clean()

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

            success = apply_propagation_context(request.headers)

            if not success:
                return JsonResponse(
                    make_error_response(
                        "AUTH_GATE_DENIED",
                        "Требуются действительные заголовки X-Tenant-ID / X-User-ID.",
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
            ContextGuard.hard_reset()
