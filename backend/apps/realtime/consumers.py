import asyncio
import json
import uuid

import jwt
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


def _decode_jwt(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None


def _incr_ws_counter() -> None:
    from apps.common.infra.redis import get_default_redis
    get_default_redis().incr("ws:active_connections")


def _decr_ws_counter() -> None:
    from apps.common.infra.redis import get_default_redis
    r = get_default_redis()
    current = r.get("ws:active_connections")
    if current and int(current) > 0:
        r.decr("ws:active_connections")


class ErpRealtimeConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.tenant_id = None
        self.stream_name = None
        self.files_group = None
        self.notifications_group = None
        self.is_negotiated = False
        await self.accept()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _incr_ws_counter)

    async def disconnect(self, close_code):
        if self.files_group is not None:
            await self.channel_layer.group_discard(self.files_group, self.channel_name)
        if self.notifications_group is not None:
            await self.channel_layer.group_discard(self.notifications_group, self.channel_name)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _decr_ws_counter)
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

        tenant_id_str = ctx_data.get("tenant_id")
        user_id_str = ctx_data.get("user_id")

        # Browser clients can't send custom headers — accept JWT from connection_init payload.
        # Gateway-injected headers take precedence (more trusted).
        if not tenant_id_str:
            token = payload.get("token")
            if not token:
                await self.send(json.dumps({"type": "error", "message": "MISSING_GATEWAY_CONTEXT"}))
                await self.close(code=4401)
                return
            jwt_payload = _decode_jwt(token)
            if not jwt_payload:
                await self.send(json.dumps({"type": "error", "message": "INVALID_TOKEN"}))
                await self.close(code=4401)
                return
            tenant_id_str = jwt_payload.get("tenant_id")
            user_id_str = jwt_payload.get("user_id")
            if not tenant_id_str:
                await self.send(json.dumps({"type": "error", "message": "MISSING_GATEWAY_CONTEXT"}))
                await self.close(code=4401)
                return

        set_tenant_id(uuid.UUID(tenant_id_str))
        if user_id_str:
            set_user_id(uuid.UUID(user_id_str))
        if ctx_data.get("request_id"):
            set_request_id(uuid.UUID(ctx_data["request_id"]))
        if ctx_data.get("correlation_id"):
            set_correlation_id(uuid.UUID(ctx_data["correlation_id"]))

        self.scope["headers_ctx"] = raw_headers
        self.tenant_id = tenant_id_str
        self.stream_name = f"erp:streams:tenant:{self.tenant_id}"
        self.is_negotiated = True

        # Join channel groups for tenant-scoped fanout
        self.files_group = f"files.tenant.{self.tenant_id}"
        await self.channel_layer.group_add(self.files_group, self.channel_name)

        self.notifications_group = f"notifications.tenant.{self.tenant_id}"
        await self.channel_layer.group_add(self.notifications_group, self.channel_name)

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

    async def files_file_status_changed(self, event: dict):
        """Channel layer handler: broadcasts file status change to the WebSocket client."""
        await self.send(
            json.dumps(
                {
                    "type": "realtime_event",
                    "event_type": "file.status_changed",
                    "payload": {
                        "file_id": event["file_id"],
                        "status": event["status"],
                    },
                }
            )
        )

    async def notifications_new(self, event: dict):
        """Channel layer handler: broadcasts new notification to the WebSocket client."""
        await self.send(
            json.dumps(
                {
                    "type": "realtime_event",
                    "event_type": "notification.new",
                    "payload": {
                        "user_id": event["user_id"],
                        "notification_id": event["notification_id"],
                    },
                }
            )
        )
