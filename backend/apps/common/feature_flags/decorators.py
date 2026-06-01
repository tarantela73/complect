from functools import wraps

from django.http import JsonResponse

from apps.common.feature_flags.repository import FeatureFlagRepository


def enforce_feature_gate(flag_name: str):
    """
    [ИСПРАВЛЕНО] Декоратор защиты API ручек через Feature Flags.
    Принудительно возвращает HTTP 503 Service Unavailable, если фича отключена.
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            # Проверка флага через быстрый Redis-репозиторий
            if not FeatureFlagRepository.is_flag_active(flag_name):
                return JsonResponse(
                    {
                        "code": "FEATURE_DISABLED_BY_GOVERNANCE",
                        "message": f"Компонент '{flag_name}' временно недоступен или отключен СБ/SRE командой.",
                    },
                    status=503,
                )

            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator
