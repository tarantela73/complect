import json
from typing import Callable

import structlog

logger = structlog.get_logger(__name__)

# Infrastructure exceptions that indicate systemic failure, not business errors.
# ValidationError, PermissionDenied, Http404 are excluded — they are expected flows.
_INFRA_EXC_NAMES: frozenset[str] = frozenset({
    "DatabaseError",
    "OperationalError",
    "InterfaceError",
    "InternalError",
    "ProgrammingError",
    "ConnectionError",
    "TimeoutError",
    "ConnectionRefusedError",
    "BrokenPipeError",
    "RedisError",
    "ResponseError",
})


def _is_infrastructure_exception(exc: BaseException) -> bool:
    return type(exc).__name__ in _INFRA_EXC_NAMES or isinstance(exc, OSError)


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
        except Exception as exc:
            if _is_infrastructure_exception(exc):
                self._emit_routing(exc)
            await self._send_500(send)

    @staticmethod
    def _emit_routing(exc: BaseException) -> None:
        from apps.common.services.incident_routing import IncidentContext, IncidentRoutingPolicy

        try:
            ctx = IncidentContext(
                incidents=(),
                chain_slo_violated=True,
                affected_domains=frozenset({"infrastructure"}),
            )
            decision = IncidentRoutingPolicy.route(ctx)
            logger.critical(
                "infrastructure_exception_routed",
                exc_type=type(exc).__name__,
                **decision.structlog_labels,
            )
        except Exception:
            pass  # routing must never mask the original exception

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
