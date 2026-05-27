import structlog
from django.http import JsonResponse
from apps.common.feature_flags.repository import FeatureFlagRepository

logger = structlog.get_logger(__name__)


class SreGlobalKillSwitchMiddleware:
    """
    SRE global traffic circuit breaker.
    """

    DOMAIN_FLAGS_MAP = {
        "/api/v1/analytics/": "kill_switch_heavy_analytics",
        "/api/v1/chat/": "kill_switch_realtime_chat",
        "/api/v1/export/": "kill_switch_reports_export",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info

        for route_prefix, flag_name in self.DOMAIN_FLAGS_MAP.items():
            if path.startswith(route_prefix):
                try:
                    is_enabled = FeatureFlagRepository.is_flag_active(flag_name)
                except Exception:
                    # fail-safe strategy (usually fail-open for availability layer)
                    return self.get_response(request)

                if not is_enabled:
                    logger.critical(
                        "SRE_KILL_SWITCH_TRIGGERED",
                        path=path,
                        flag_name=flag_name,
                        correlation_id=request.headers.get("X-Correlation-ID", "unknown"),
                    )

                    segments = path.split("/")
                    domain = segments[3] if len(segments) > 3 else "unknown"

                    return JsonResponse(
                        {
                            "code": "DOMAIN_TEMPORARILY_DISABLED",
                            "message": f"Домен '{domain}' временно отключен SRE системой.",
                        },
                        status=503,
                    )

                break

        return self.get_response(request)
