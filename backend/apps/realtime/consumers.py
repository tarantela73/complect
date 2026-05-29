import asyncio
import json
import uuid

import structlog
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from apps.common.context.core import (
    clear_context,
    set_correlation_id,
    set_request_id,
    set_tenant_id,
    set_user_id,
)
from apps.common.context.propagation.http import extract_http_context

logger = structlog.get_logger(__name__)


class ErpRealtimeConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.tenant_id = None
        self.stream_name = None
        self.is_negotiated = False
        await self.accept()

    async def disconnect(self, close_code):
        logger.info("websocket_disconnected", close_code=close_code)
        clear_context()

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

        raw_headers = self.scope.get("headers_ctx", {})
        ctx_data = extract_http_context(raw_headers)
        if ctx_data.get("tenant_id"):
            set_tenant_id(uuid.UUID(ctx_data["tenant_id"]))

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

        raw_headers = {k.decode("utf-8"): v.decode("utf-8") for k, v in self.scope.get("headers", [])}
        ctx_data = extract_http_context(raw_headers)

        if not ctx_data.get("tenant_id"):
            await self.send(json.dumps({"type": "error", "message": "MISSING_GATEWAY_CONTEXT"}))
            await self.close(code=4401)
            return

        set_tenant_id(uuid.UUID(ctx_data["tenant_id"]))
        if ctx_data.get("user_id"):
            set_user_id(uuid.UUID(ctx_data["user_id"]))
        if ctx_data.get("request_id"):
            set_request_id(uuid.UUID(ctx_data["request_id"]))
        if ctx_data.get("correlation_id"):
            set_correlation_id(uuid.UUID(ctx_data["correlation_id"]))

        self.scope["headers_ctx"] = raw_headers
        self.tenant_id = ctx_data["tenant_id"]
        self.stream_name = f"erp:streams:tenant:{self.tenant_id}"
        self.is_negotiated = True

        negotiated_capabilities = [cap for cap in ["at_least_once", "event_replay"] if cap in capabilities]

        await self.send(
            json.dumps(
                {
                    "type": "connection_ack",
                    "protocol_version": 1,
                    "negotiated_capabilities": negotiated_capabilities,
                }
            )
        )

        last_sequence_id = payload.get("last_sequence_id")
        if last_sequence_id and "event_replay" in negotiated_capabilities:
            await self._replay_missed_events(last_sequence_id=last_sequence_id)

    async def _replay_missed_events(self, last_sequence_id: str):

        redis_client = settings.REDIS_CLIENT

        # Запускаем синхронный Redis в executor чтобы не блокировать event loop
        loop = asyncio.get_event_loop()
        raw_stream = await loop.run_in_executor(
            None, lambda: redis_client.xread({self.stream_name: last_sequence_id}, count=50)
        )

        if not raw_stream:
            return

        for _, stream_records in raw_stream:
            for sequence_id, fields in stream_records:
                await self.send(
                    json.dumps(
                        {
                            "type": "realtime_event",
                            "sequence_id": sequence_id,
                            "event_type": fields["event_type"],
                            "payload": json.loads(fields["payload"]),
                            "is_replay": True,
                        }
                    )
                )
