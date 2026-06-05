from django.db import models

from apps.common.models.base import BaseEnterpriseModel
from apps.integration.models.webhook_subscription import WebhookSubscription


class WebhookDeliveryStatus(models.TextChoices):
    PENDING = "PENDING", "Ожидает"
    DELIVERED = "DELIVERED", "Доставлено"
    FAILED = "FAILED", "Ошибка (retry)"
    DEAD = "DEAD", "Исчерпаны попытки"


class WebhookDelivery(BaseEnterpriseModel):
    subscription = models.ForeignKey(
        WebhookSubscription,
        on_delete=models.CASCADE,
        related_name="deliveries",
        db_index=True,
    )
    outbox_record_id = models.UUIDField(db_index=True)
    event_type = models.CharField(max_length=255)
    event_id = models.UUIDField()
    attempt = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(
        max_length=30,
        choices=WebhookDeliveryStatus.choices,
        default=WebhookDeliveryStatus.PENDING,
        db_index=True,
    )
    response_code = models.SmallIntegerField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    payload_sha256 = models.CharField(max_length=64)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    error_detail = models.TextField(blank=True, default="")

    class Meta:
        db_table = "integration_webhook_deliveries"
        indexes = [
            models.Index(
                fields=["tenant_id", "subscription_id", "status"],
                name="wh_del_tenant_sub_status_idx",
            ),
            models.Index(
                fields=["outbox_record_id", "subscription_id"],
                name="wh_del_outbox_sub_idx",
            ),
        ]
