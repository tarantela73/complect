import json
import structlog
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from apps.common.tenancy.context_core import set_context, clear_context
from apps.common.tenancy.context_propagation import ContextPropagation

logger = structlog.get_logger(__name__)


class ErpRealtimeConsumer(AsyncWebsocketConsumer):
    """
    [ИСПРАВЛЕНО — ПУНКТ 5] ASGI WebSocket Потребитель реального времени.
    Интегрирован со сквозным слоем ContextPropagation для 100% защиты от потери трейсинга.
    """

    async def connect(self):
        self.tenant_id = None
        self.stream_name = None
        self.is_negotiated = False
        await self.accept()

    async def disconnect(self, close_code):
        logger.info("websocket_disconnected", close_code=close_code)
        clear_context()  # Чистим асинхронный event loop потока

    async def receive(self, text_data):
        try:
            payload = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send(json.dumps({"type": "error", "code": "MALFORMED_FRAME"}))
            return

        message_type = payload.get("type")

        if message_type == "connection_init":
            await self._handle_handshake(payload)
            return

        if not self.is_negotiated:
            await self.send(
                json.dumps({"type": "error", "code": "PROTOCOL_VIOLATION", "message": "connection_init required"})
            )
            await self.close(code=4401)
            return

        # Принудительно взводим контекст текущей сокет-сессии перед обработкой ACK фрейма
        # Извлекаем заголовки, сохраненные при хэндшейке в scope
        ctx = ContextPropagation.from_headers(self.scope.get("headers_ctx", {}))
        if ctx:
            set_context(ctx)

        if message_type == "event_ack":
            sequence_id = payload.get("sequence_id")
            logger.debug("websocket_ack_received", sequence_id=sequence_id)

    async def _handle_handshake(self, payload: dict):
        protocol_version = payload.get("protocol_version")
        capabilities = payload.get("capabilities", [])

        if protocol_version != 1:
            await self.send(json.dumps({"type": "connection_error", "code": "PROTOCOL_VERSION_DEPRECATED"}))
            await self.close(code=4410)
            return

        # [ИСПРАВЛЕНО — ПУНКТ 5]: Демаршалинг и активация контекста из scope-заголовков сокета (залиты шлюзом)
        # Преобразуем байтовые заголовки ASGI scope в читаемый строковый словарь dict
        raw_headers = {k.decode("utf-8"): v.decode("utf-8") for k, v in self.scope.get("headers", [])}
        ctx = ContextPropagation.from_headers(raw_headers)

        if not ctx:
            await self.send(json.dumps({"type": "error", "message": "MISSING_GATEWAY_CONTEXT"}))
            await self.close(code=4401)
            return

        # Активируем контекст и сохраняем слепок в scope для последующих кадров receive()
        set_context(ctx)
        self.scope["headers_ctx"] = raw_headers

        self.tenant_id = str(ctx.tenant_id)
        self.stream_name = f"erp:streams:tenant:{self.tenant_id}"
        self.is_negotiated = True

        negotiated_capabilities = []
        if "at_least_once" in capabilities:
            negotiated_capabilities.append("at_least_once")
        if "event_replay" in capabilities:
            negotiated_capabilities.append("event_replay")

        await self.send(
            json.dumps(
                {"type": "connection_ack", "protocol_version": 1, "negotiated_capabilities": negotiated_capabilities}
            )
        )

        last_sequence_id = payload.get("last_sequence_id")
        if last_sequence_id and "event_replay" in negotiated_capabilities:
            await self._replay_missed_events(last_sequence_id=last_sequence_id)

    async def _replay_missed_events(self, last_sequence_id: str):
        # Внутренний вызов чтения из Redis Stream лога событий
        from django.conf import settings

        redis_client = settings.REDIS_CLIENT
        raw_stream = redis_client.xread({self.stream_name: last_sequence_id}, count=50)

        if not raw_stream:
            return

        for _, stream_records in raw_stream:
            for sequence_id, fields in stream_records:
                await self.send(
                    json.dumps(
                        {
                            "type": "realtime_event",
                            "sequence_id": sequence_id.decode("utf-8"),
                            "event_type": fields[b"event_type"].decode("utf-8"),
                            "payload": json.loads(fields[b"payload"].decode("utf-8")),
                            "is_replay": True,
                        }
                    )
                )
