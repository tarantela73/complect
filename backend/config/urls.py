from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # [ИСПРАВЛЕНО]: Системный инжект K8s-совместимого эндпоинта хелсчека
    path("", include("config.health_urls")),
]
