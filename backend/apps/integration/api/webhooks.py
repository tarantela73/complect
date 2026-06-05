import uuid
from typing import Optional

from django.http import HttpRequest
from ninja import Query, Router, Schema

from apps.common.api.errors import make_error_response
from apps.common.api.permissions import require_permission
from apps.common.events.registry import ALL_DOMAIN_EVENT_TYPES

router = Router(tags=["webhooks"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class WebhookSubscriptionOut(Schema):
    id: uuid.UUID
    url: str
    event_types: list[str]
    is_active: bool
    last_delivery_at: Optional[str] = None
    created_at: str

    @staticmethod
    def from_model(obj) -> "WebhookSubscriptionOut":
        return WebhookSubscriptionOut(
            id=obj.id,
            url=obj.url,
            event_types=obj.event_types,
            is_active=obj.is_active,
            last_delivery_at=obj.last_delivery_at.isoformat() if obj.last_delivery_at else None,
            created_at=obj.created_at.isoformat(),
        )


class WebhookSubscriptionCreate(Schema):
    url: str
    secret: str
    event_types: list[str]


class WebhookSubscriptionUpdate(Schema):
    url: Optional[str] = None
    secret: Optional[str] = None
    event_types: Optional[list[str]] = None
    is_active: Optional[bool] = None


class WebhookDeliveryOut(Schema):
    id: uuid.UUID
    subscription_id: uuid.UUID
    event_type: str
    event_id: uuid.UUID
    attempt: int
    status: str
    response_code: Optional[int] = None
    duration_ms: Optional[int] = None
    payload_sha256: str
    next_retry_at: Optional[str] = None
    delivered_at: Optional[str] = None
    error_detail: str
    created_at: str

    @staticmethod
    def from_model(obj) -> "WebhookDeliveryOut":
        return WebhookDeliveryOut(
            id=obj.id,
            subscription_id=obj.subscription_id,
            event_type=obj.event_type,
            event_id=obj.event_id,
            attempt=obj.attempt,
            status=obj.status,
            response_code=obj.response_code,
            duration_ms=obj.duration_ms,
            payload_sha256=obj.payload_sha256,
            next_retry_at=obj.next_retry_at.isoformat() if obj.next_retry_at else None,
            delivered_at=obj.delivered_at.isoformat() if obj.delivered_at else None,
            error_detail=obj.error_detail,
            created_at=obj.created_at.isoformat(),
        )


class WebhookListOut(Schema):
    items: list[WebhookSubscriptionOut]
    next_cursor: Optional[uuid.UUID] = None


class DeliveryListOut(Schema):
    items: list[WebhookDeliveryOut]
    next_cursor: Optional[uuid.UUID] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_tenant_id(request: HttpRequest) -> uuid.UUID:
    return uuid.UUID(str(request.jwt_tenant_id))


def _validate_event_types(event_types: list[str]) -> list[str]:
    unknown = set(event_types) - ALL_DOMAIN_EVENT_TYPES
    if unknown:
        raise ValueError(f"Unknown event types: {sorted(unknown)}")
    return event_types


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/", response={200: WebhookListOut, 403: dict})
@require_permission("read", "webhooks")
def list_webhooks(
    request: HttpRequest,
    cursor: Optional[uuid.UUID] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    from apps.integration.models import WebhookSubscription

    tenant_id = _get_tenant_id(request)
    qs = WebhookSubscription.objects.filter(
        tenant_id=tenant_id,
        is_deleted=False,
    ).order_by("-created_at", "-id")

    if cursor:
        anchor = WebhookSubscription.objects.filter(id=cursor, tenant_id=tenant_id).values("created_at").first()
        if anchor:
            qs = qs.filter(created_at__lt=anchor["created_at"])

    items = list(qs[: limit + 1])
    next_cursor = items[limit].id if len(items) > limit else None
    return 200, WebhookListOut(
        items=[WebhookSubscriptionOut.from_model(s) for s in items[:limit]],
        next_cursor=next_cursor,
    )


@router.post("/", response={201: WebhookSubscriptionOut, 400: dict, 403: dict})
@require_permission("write", "webhooks")
def create_webhook(request: HttpRequest, body: WebhookSubscriptionCreate):
    from apps.integration.models import WebhookSubscription

    try:
        _validate_event_types(body.event_types)
    except ValueError as exc:
        return 400, make_error_response("INVALID_EVENT_TYPES", str(exc))

    tenant_id = _get_tenant_id(request)
    sub = WebhookSubscription.objects.create(
        tenant_id=tenant_id,
        url=body.url,
        secret=body.secret,
        event_types=body.event_types,
        is_active=True,
    )
    return 201, WebhookSubscriptionOut.from_model(sub)


@router.get("/{sub_id}/", response={200: WebhookSubscriptionOut, 403: dict, 404: dict})
@require_permission("read", "webhooks")
def get_webhook(request: HttpRequest, sub_id: uuid.UUID):
    from apps.integration.models import WebhookSubscription

    tenant_id = _get_tenant_id(request)
    try:
        sub = WebhookSubscription.objects.get(id=sub_id, tenant_id=tenant_id, is_deleted=False)
    except WebhookSubscription.DoesNotExist:
        return 404, make_error_response("WEBHOOK_NOT_FOUND", "Подписка не найдена.")
    return 200, WebhookSubscriptionOut.from_model(sub)


@router.patch("/{sub_id}/", response={200: WebhookSubscriptionOut, 400: dict, 403: dict, 404: dict})
@require_permission("write", "webhooks")
def update_webhook(request: HttpRequest, sub_id: uuid.UUID, body: WebhookSubscriptionUpdate):
    from apps.integration.models import WebhookSubscription

    tenant_id = _get_tenant_id(request)
    try:
        sub = WebhookSubscription.objects.get(id=sub_id, tenant_id=tenant_id, is_deleted=False)
    except WebhookSubscription.DoesNotExist:
        return 404, make_error_response("WEBHOOK_NOT_FOUND", "Подписка не найдена.")

    if sub.is_legal_hold:
        return 403, make_error_response("LEGAL_HOLD", "Подписка заблокирована юридическим удержанием.")

    updates: dict = {}
    if body.url is not None:
        updates["url"] = body.url
    if body.secret is not None:
        updates["secret"] = body.secret
    if body.event_types is not None:
        try:
            _validate_event_types(body.event_types)
        except ValueError as exc:
            return 400, make_error_response("INVALID_EVENT_TYPES", str(exc))
        updates["event_types"] = body.event_types
    if body.is_active is not None:
        updates["is_active"] = body.is_active

    if updates:
        WebhookSubscription.objects.filter(id=sub_id).update(**updates)
        sub.refresh_from_db()

    return 200, WebhookSubscriptionOut.from_model(sub)


@router.delete("/{sub_id}/", response={204: None, 403: dict, 404: dict})
@require_permission("write", "webhooks")
def delete_webhook(request: HttpRequest, sub_id: uuid.UUID):
    from apps.integration.models import WebhookSubscription

    tenant_id = _get_tenant_id(request)
    try:
        sub = WebhookSubscription.objects.get(id=sub_id, tenant_id=tenant_id, is_deleted=False)
    except WebhookSubscription.DoesNotExist:
        return 404, make_error_response("WEBHOOK_NOT_FOUND", "Подписка не найдена.")

    if sub.is_legal_hold:
        return 403, make_error_response("LEGAL_HOLD", "Подписка заблокирована юридическим удержанием.")

    WebhookSubscription.objects.filter(id=sub_id).update(is_active=False, is_deleted=True)
    return 204, None


@router.get("/{sub_id}/deliveries/", response={200: DeliveryListOut, 403: dict, 404: dict})
@require_permission("read", "webhooks")
def list_deliveries(
    request: HttpRequest,
    sub_id: uuid.UUID,
    cursor: Optional[uuid.UUID] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    from apps.integration.models import WebhookDelivery, WebhookSubscription

    tenant_id = _get_tenant_id(request)
    if not WebhookSubscription.objects.filter(id=sub_id, tenant_id=tenant_id).exists():
        return 404, make_error_response("WEBHOOK_NOT_FOUND", "Подписка не найдена.")

    qs = WebhookDelivery.objects.filter(
        subscription_id=sub_id,
        tenant_id=tenant_id,
    ).order_by("-created_at", "-id")

    if cursor:
        anchor = WebhookDelivery.objects.filter(id=cursor, tenant_id=tenant_id).values("created_at").first()
        if anchor:
            qs = qs.filter(created_at__lt=anchor["created_at"])

    items = list(qs[: limit + 1])
    next_cursor = items[limit].id if len(items) > limit else None
    return 200, DeliveryListOut(
        items=[WebhookDeliveryOut.from_model(d) for d in items[:limit]],
        next_cursor=next_cursor,
    )
