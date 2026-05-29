# config/urls.py
from django.http import JsonResponse
from django.urls import include, path

from apps.common.feature_flags.decorators import enforce_feature_gate


@enforce_feature_gate("kill_switch_heavy_analytics")
def analytics_dashboard_stub(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("", include("config.health_urls")),
    path("api/v1/contracts/", lambda _r: JsonResponse({"status": "ok"})),
    path("api/v1/contracts/dashboard_stub", lambda _r: JsonResponse({"status": "ok"})),
    path("api/v1/contracts/create_stub", lambda _r: JsonResponse({"status": "ok"}, status=201)),
    path("api/v1/analytics/dashboard_stub", analytics_dashboard_stub),
    path("api/v1/analytics/any_unprotected_endpoint_stub", lambda _r: JsonResponse({"status": "ok"})),
]
