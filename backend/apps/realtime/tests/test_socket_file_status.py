"""
WebSocket file status broadcast tests.

Verifies that ErpRealtimeConsumer forwards channel layer group_send messages
(originating from FilesConsumer._broadcast_file_status on_commit) to the WS client.
"""
from __future__ import annotations

import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator

from config.asgi import application

_TENANT_ID = "11111111-1111-4111-a111-111111111111"
_USER_ID = "22222222-2222-4222-a222-222222222222"
_GATEWAY_HEADERS = [
    (b"x-gateway-token", b"complect_internal_secure_mesh_token_2026"),
    (b"x-tenant-id", _TENANT_ID.encode()),
    (b"x-user-id", _USER_ID.encode()),
    (b"x-request-id", b"33333333-3333-4333-a333-333333333333"),
    (b"x-correlation-id", b"44444444-4444-4444-a444-444444444444"),
]


async def _connect_and_negotiate(extra_capabilities: list[str] | None = None) -> WebsocketCommunicator:
    communicator = WebsocketCommunicator(application, "/ws/realtime/", headers=_GATEWAY_HEADERS)
    connected, _ = await communicator.connect()
    assert connected

    await communicator.send_json_to(
        {
            "type": "connection_init",
            "protocol_version": 1,
            "capabilities": extra_capabilities or ["at_least_once"],
        }
    )
    ack = await communicator.receive_json_from()
    assert ack["type"] == "connection_ack"
    return communicator


@pytest.mark.asyncio
class TestFileStatusWebSocketBroadcast:
    async def test_file_available_event_delivered(self):
        """[WS/FILES] AVAILABLE broadcast reaches the connected client."""
        communicator = await _connect_and_negotiate()

        file_id = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"files.tenant.{_TENANT_ID}",
            {"type": "files_file_status_changed", "file_id": file_id, "status": "AVAILABLE"},
        )

        msg = await communicator.receive_json_from(timeout=3)
        assert msg["type"] == "realtime_event"
        assert msg["event_type"] == "file.status_changed"
        assert msg["payload"]["file_id"] == file_id
        assert msg["payload"]["status"] == "AVAILABLE"

        await communicator.disconnect()

    async def test_file_quarantined_event_delivered(self):
        """[WS/FILES] QUARANTINED broadcast reaches the connected client."""
        communicator = await _connect_and_negotiate()

        file_id = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"files.tenant.{_TENANT_ID}",
            {"type": "files_file_status_changed", "file_id": file_id, "status": "QUARANTINED"},
        )

        msg = await communicator.receive_json_from(timeout=3)
        assert msg["type"] == "realtime_event"
        assert msg["event_type"] == "file.status_changed"
        assert msg["payload"]["status"] == "QUARANTINED"

        await communicator.disconnect()

    async def test_cross_tenant_isolation(self):
        """[WS/FILES/SECURITY] Events from another tenant are NOT delivered to this client."""
        communicator = await _connect_and_negotiate()

        other_tenant = "99999999-9999-4999-a999-999999999999"
        file_id = "cccccccc-cccc-4ccc-cccc-cccccccccccc"
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"files.tenant.{other_tenant}",
            {"type": "files_file_status_changed", "file_id": file_id, "status": "AVAILABLE"},
        )

        # No message should arrive for our client
        assert await communicator.receive_nothing(timeout=0.3)

        await communicator.disconnect()

    async def test_disconnect_leaves_group(self):
        """[WS/FILES] Disconnect silently leaves the channel group (no error)."""
        communicator = await _connect_and_negotiate()
        await communicator.disconnect()

        channel_layer = get_channel_layer()
        # group_send to a now-empty group must not raise
        await channel_layer.group_send(
            f"files.tenant.{_TENANT_ID}",
            {
                "type": "files_file_status_changed",
                "file_id": "dddddddd-dddd-4ddd-dddd-dddddddddddd",
                "status": "AVAILABLE",
            },
        )

    async def test_jwt_fallback_auth_browser_client(self):
        """[WS/FILES/AUTH] Browser clients without gateway headers can auth via JWT payload."""
        import jwt as pyjwt
        from django.conf import settings

        token = pyjwt.encode(
            {"user_id": _USER_ID, "tenant_id": _TENANT_ID, "type": "access"},
            settings.SECRET_KEY,
            algorithm="HS256",
        )

        communicator = WebsocketCommunicator(application, "/ws/realtime/")
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {"type": "connection_init", "protocol_version": 1, "capabilities": [], "token": token}
        )
        ack = await communicator.receive_json_from()
        assert ack["type"] == "connection_ack"

        file_id = "eeeeeeee-eeee-4eee-aeee-eeeeeeeeeeee"
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"files.tenant.{_TENANT_ID}",
            {"type": "files_file_status_changed", "file_id": file_id, "status": "AVAILABLE"},
        )

        msg = await communicator.receive_json_from(timeout=3)
        assert msg["payload"]["file_id"] == file_id

        await communicator.disconnect()
