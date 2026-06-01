from django.urls import include, path

from apps.contracts.api import router as contracts_router
from apps.users.api import router as auth_router
from config.api import api

api.add_router("/auth/", auth_router)
api.add_router("/contracts/", contracts_router)

urlpatterns = [
    path("", include("config.health_urls")),
    path("api/v1/", api.urls),
]
