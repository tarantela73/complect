# backend/apps/common/tenancy/middleware.py
from django.conf import settings
from django.http import JsonResponse

from apps.common.context.guard import ContextGuard
from apps.common.context.propagation import apply_propagation_context


class AsgiTenantEnforcementMiddleware:
    PUBLIC_PREFIXES = (
        "/public/",
        "/static/",
        "/metrics/",
        "/health/",
        "/favicon.ico",
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

            # GATEWAY TRUST BOUNDARY
            expected_token = getattr(settings, "EXPECTED_GATEWAY_TOKEN", None)
            if expected_token:
                gateway_token = request.headers.get("X-Gateway-Token")
                if gateway_token != expected_token:
                    return JsonResponse(
                        {"code": "GATEWAY_TRUST_VIOLATION"},
                        status=403,
                    )

            success = apply_propagation_context(request.headers)

            if not success:
                return JsonResponse(
                    {
                        "code": "AUTH_GATE_DENIED",
                        "message": "Missing context headers: Требуются действительные X-Tenant-ID / X-User-ID",
                    },
                    status=401,
                )

            ContextGuard.validate_consistency()
            return self.get_response(request)

        except (ValueError, TypeError) as e:
            return JsonResponse(
                {"code": "MALFORMED_CONTEXT_HEADERS", "message": str(e)},
                status=400,
            )

        finally:
            ContextGuard.hard_reset()
