# backend/config/asgi.py
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

django_asgi_app = get_asgi_application()

from apps.common.middleware.asgi_error import AsgiErrorBoundaryMiddleware  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from apps.realtime.routing import websocket_urlpatterns  # noqa: E402

application = AsgiErrorBoundaryMiddleware(
    ProtocolTypeRouter(
        {
            "http": django_asgi_app,
            "websocket": URLRouter(websocket_urlpatterns),
        }
    )
)
