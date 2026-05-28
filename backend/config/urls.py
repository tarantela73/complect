# config/urls.py
from django.urls import include, path

urlpatterns = [
    path("", include("config.health_urls")),
]
