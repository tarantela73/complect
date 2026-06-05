import uuid

import structlog
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = structlog.get_logger(__name__)

_WS_GROUP = "notifications.tenant.{tenant_id}"


def push_notification_ws(
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    notification_id: uuid.UUID,
) -> None:
    """Fanout new notification to all WebSocket clients of the tenant."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    try:
        async_to_sync(channel_layer.group_send)(
            _WS_GROUP.format(tenant_id=tenant_id),
            {
                "type": "notifications_new",
                "user_id": str(user_id),
                "notification_id": str(notification_id),
            },
        )
    except Exception as exc:
        logger.warning(
            "ws_notification_push_failed",
            notification_id=str(notification_id),
            user_id=str(user_id),
            error=str(exc),
        )
