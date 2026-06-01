from django.http import HttpRequest, JsonResponse
from ninja.errors import AuthenticationError, ValidationError


def ninja_validation_error_handler(request: HttpRequest, exc: ValidationError):
    return JsonResponse(
        {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Ошибка валидации входных данных.",
                "detail": exc.errors,
                "request_id": getattr(request, "request_id", None),
                "correlation_id": getattr(request, "correlation_id", None),
            }
        },
        status=400,
    )


def ninja_auth_error_handler(request: HttpRequest, exc: AuthenticationError):
    return JsonResponse(
        {
            "error": {
                "code": "AUTHENTICATION_REQUIRED",
                "message": "Не аутентифицирован.",
                "detail": None,
                "request_id": getattr(request, "request_id", None),
                "correlation_id": getattr(request, "correlation_id", None),
            }
        },
        status=401,
    )
