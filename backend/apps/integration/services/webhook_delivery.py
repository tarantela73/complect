from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

import structlog
from django.utils import timezone as tz

logger = structlog.get_logger(__name__)

# Retry schedule (seconds): 1m, 5m, 15m, 1h, 6h
RETRY_DELAYS: tuple[int, ...] = (60, 300, 900, 3600, 21600)

_REQUEST_TIMEOUT = 30


class WebhookDeliveryError(Exception):
    """Raised on non-2xx response or network error — triggers Celery retry."""


def _sign_payload(secret: str, payload: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def _build_delivery_payload(delivery) -> dict:
    return {
        "delivery_id": str(delivery.id),
        "event_type": delivery.event_type,
        "event_id": str(delivery.event_id),
        "attempt": delivery.attempt,
        "occurred_at": delivery.created_at.isoformat(),
        "payload": {},  # actual event payload stored in outbox; consumer sets it via context
    }


class WebhookDeliveryService:
    """Execute a single webhook delivery attempt."""

    @classmethod
    def execute(cls, delivery_id: uuid.UUID, payload_override: dict | None = None) -> None:
        import requests

        from apps.integration.models import WebhookDelivery, WebhookDeliveryStatus, WebhookSubscription

        delivery = WebhookDelivery.objects.select_related("subscription").get(id=delivery_id)
        if delivery.status == WebhookDeliveryStatus.DELIVERED:
            return

        subscription: WebhookSubscription = delivery.subscription
        if not subscription.is_active:
            WebhookDelivery.objects.filter(id=delivery_id).update(
                status=WebhookDeliveryStatus.DEAD,
                error_detail="Subscription deactivated before delivery",
            )
            return

        body: dict = payload_override if payload_override is not None else _build_delivery_payload(delivery)
        payload_bytes = json.dumps(body, default=str).encode()
        signature = _sign_payload(subscription.secret, payload_bytes)

        t0 = time.perf_counter()
        try:
            resp = requests.post(
                subscription.url,
                data=payload_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Complect-Signature": signature,
                    "X-Complect-Event": delivery.event_type,
                    "X-Complect-Delivery": str(delivery.id),
                },
                timeout=_REQUEST_TIMEOUT,
            )
            duration_ms = int((time.perf_counter() - t0) * 1000)
        except requests.RequestException as exc:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            WebhookDelivery.objects.filter(id=delivery_id).update(
                duration_ms=duration_ms,
                error_detail=str(exc)[:2000],
            )
            raise WebhookDeliveryError(str(exc)) from exc

        if resp.status_code < 400:
            now = tz.now()
            WebhookDelivery.objects.filter(id=delivery_id).update(
                status=WebhookDeliveryStatus.DELIVERED,
                response_code=resp.status_code,
                duration_ms=duration_ms,
                delivered_at=now,
                error_detail="",
            )
            WebhookSubscription.objects.filter(id=subscription.id).update(
                last_delivery_at=now,
            )
            logger.info(
                "webhook_delivered",
                delivery_id=str(delivery_id),
                subscription_id=str(subscription.id),
                event_type=delivery.event_type,
                status_code=resp.status_code,
                duration_ms=duration_ms,
                attempt=delivery.attempt,
            )
        else:
            WebhookDelivery.objects.filter(id=delivery_id).update(
                response_code=resp.status_code,
                duration_ms=duration_ms,
                error_detail=resp.text[:2000],
            )
            raise WebhookDeliveryError(f"HTTP {resp.status_code}") from None
