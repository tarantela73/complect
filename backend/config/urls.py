from django.urls import include, path

from apps.common.api.dashboard import router as dashboard_router
from apps.common.api.errors import (
    django_bad_request,
    django_page_not_found,
    django_permission_denied,
    django_server_error,
)
from apps.contracts.api import router as contracts_router
from apps.contragents.api import router as contragents_router
from apps.files.api.files_api import router as files_router
from apps.finance.api import router as finance_router
from apps.objects.api import router as objects_router
from apps.users.api import router as auth_router
from config.api import api

api.add_router("/auth/", auth_router)
api.add_router("/dashboard/", dashboard_router)
api.add_router("/contracts/", contracts_router)
api.add_router("/contragents/", contragents_router)
api.add_router("/finance/", finance_router)
api.add_router("/objects/", objects_router)
api.add_router("/files/", files_router)

urlpatterns = [
    path("", include("config.health_urls")),
    path("api/v1/", api.urls),
]

# Django fallback error handlers — enforce rule 6.6 envelope for all paths
# that bypass Ninja (CSRF failure, unknown URL, uncaught server error).
handler400 = django_bad_request
handler403 = django_permission_denied
handler404 = django_page_not_found
handler500 = django_server_error
