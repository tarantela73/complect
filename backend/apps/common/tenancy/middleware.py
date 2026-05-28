from django.http import JsonResponse
from apps.common.context.guard import ContextGuard
from apps.common.context.propagation import apply_propagation_context


class AsgiTenantEnforcementMiddleware:
    """
    [ФИНАЛЬНЫЙ PRODUCTION КАНОН v3.7.4]
    Strict tenant isolation boundary & orchestration layer.
    Выполняет fail-closed проверку паспортных данных и блокирует неавторизованный трафик.
    """

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
        # 1. CORS Preflight Bypass: Ставим строго ДО проверки утечек и try-зоны
        if request.method == "OPTIONS":
            return self.get_response(request)

        path = request.path_info

        # 2. Public Routes & K8s Liveness Probes Bypass
        if path.startswith(self.PUBLIC_PREFIXES):
            return self.get_response(request)

        try:
            # 3. HARD CONTEXT LEAK CHECK (Защищен внутренним try/finally блоком)
            ContextGuard.assert_clean()

            # 4. PROPAGATION LAYER (Демаршалинг паспортных заголовков)
            success = apply_propagation_context(request.headers)

            if not success:
                return JsonResponse(
                    {
                        "code": "AUTH_GATE_DENIED",
                        "message": "Missing context headers: Требуются действительные X-Tenant-ID / X-User-ID",
                    },
                    status=401,
                )

            # 5. STRICT VALIDATION (Проверка консистентности и структуры паспорта)
            ContextGuard.validate_consistency()

            return self.get_response(request)

        # [ИСПРАВЛЕНО]: Расширенный перехват исключений парсинга UUID типов данных
        except (ValueError, TypeError):
            return JsonResponse(
                {"code": "MALFORMED_CONTEXT_HEADERS"},
                status=400,
            )

        finally:
            # ABSOLUTE RESET (Выполняется всегда, дублируя выжигание на транспортном уровне)
            ContextGuard.hard_reset()
