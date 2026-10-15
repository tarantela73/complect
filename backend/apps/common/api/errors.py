from django.http import HttpRequest, JsonResponse
from ninja.errors import AuthenticationError
from ninja.errors import ValidationError as NinjaValidationError


def _ctx_ids() -> tuple[str | None, str | None]:
    try:
        from apps.common.tenancy.context_core import get_context

        ctx = get_context()
        return str(ctx.request_id), str(ctx.correlation_id)
    except Exception:
        return None, None


def make_error_response(
    code: str,
    message: str,
    detail=None,
) -> dict:
    request_id, correlation_id = _ctx_ids()
    return {
        "error": {
            "code": code,
            "message": message,
            "detail": detail,
            "request_id": request_id,
            "correlation_id": correlation_id,
        }
    }


def ninja_validation_error_handler(request: HttpRequest, exc: NinjaValidationError):
    return JsonResponse(
        make_error_response(
            code="VALIDATION_ERROR",
            message="Ошибка валидации входных данных.",
            detail=exc.errors,
        ),
        status=400,
    )


def ninja_auth_error_handler(request: HttpRequest, exc: AuthenticationError):
    return JsonResponse(
        make_error_response(
            code="AUTHENTICATION_REQUIRED",
            message="Не аутентифицирован.",
        ),
        status=401,
    )


def ninja_permission_denied_handler(request: HttpRequest, exc: Exception):
    return JsonResponse(
        make_error_response(
            code="FORBIDDEN",
            message="Доступ запрещён.",
        ),
        status=403,
    )


# ── Django-level fallback handlers (rule 6.6) ────────────────────────────────
# Wired in config/urls.py as handler400/403/404/500.
# These fire when exceptions propagate past Ninja and all middleware
# (e.g. CSRF failure, ASGI 404 for unknown paths, uncaught server errors).


def django_bad_request(request: HttpRequest, exception=None):
    return JsonResponse(
        make_error_response("BAD_REQUEST", "Некорректный запрос."),
        status=400,
    )


def django_permission_denied(request: HttpRequest, exception=None):
    return JsonResponse(
        make_error_response("FORBIDDEN", "Доступ запрещён."),
        status=403,
    )


def django_page_not_found(request: HttpRequest, exception=None):
    return JsonResponse(
        make_error_response("NOT_FOUND", "Ресурс не найден."),
        status=404,
    )


def django_server_error(request: HttpRequest):
    return JsonResponse(
        make_error_response("INTERNAL_SERVER_ERROR", "Внутренняя ошибка сервера."),
        status=500,
    )
