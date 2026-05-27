from django.http import JsonResponse
from apps.common.context.core import clear_context
from apps.common.context.guard import ContextGuard
from apps.common.context.propagation import apply_propagation_context


class AsgiTenantEnforcementMiddleware:
    """Bulletproof ИБ-барьер изоляции. Делегирует демаршалинг пакету context/propagation."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info

        # Пропуск публичных роутов
        if path.startswith("/public/") or path.startswith("/static/"):
            return self.get_response(request)

        # 🔴 РУБЕЖ 1: HARD LEAK CHECK
        ContextGuard.assert_clean()

        try:
            # Вызываем наш новый канонический демаршалер, скармливая ему request.headers
            success = apply_propagation_context(request.headers)

            if not success:
                return JsonResponse(
                    {"code": "AUTH_GATE_DENIED", "message": "Strict Context Passport required"}, status=401
                )

            # 🔴 РУБЕЖ 2: VALIDATION
            ContextGuard.validate_consistency()

            return self.get_response(request)
        except ValueError:
            return JsonResponse({"code": "MALFORMED_CONTEXT_HEADERS"}, status=400)
        finally:
            # 🔴 РУБЕЖ 3: ALWAYS CLEAN
            ContextGuard.hard_reset()
            clear_context()
