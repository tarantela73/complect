# config/urls.py
from django.urls import include, path
from django.http import JsonResponse

urlpatterns = [
    path("", include("config.health_urls")),
    path("api/v1/contracts/", lambda request: JsonResponse({"status": "ok"})),
]
