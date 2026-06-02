import json
from typing import Callable


class AsgiErrorBoundaryMiddleware:
    """
    Outermost ASGI wrapper. Catches any exception that escapes the Django
    application stack (e.g. raised inside ProtocolTypeRouter before the
    Django ASGI handler takes over) and returns a rule 6.6 envelope.

    Only active for HTTP scope — WebSocket connection failures produce no
    HTTP response, so they are intentionally left to Channels' handling.
    """

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        try:
            await self.app(scope, receive, send)
        except Exception:
            await self._send_500(send)

    @staticmethod
    async def _send_500(send: Callable) -> None:
        body = json.dumps(
            {
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "Внутренняя ошибка сервера.",
                    "detail": None,
                    "request_id": None,
                    "correlation_id": None,
                }
            }
        ).encode()

        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})
