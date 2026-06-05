from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class WebhookSubscription(BaseEnterpriseModel):
    url = models.URLField(max_length=2048)
    # HMAC-SHA256 secret — stored as-is in dev; in prod delivered via Docker Secret / Vault (rule 5.7).
    secret = models.CharField(max_length=255)
    # ["contracts.ContractCreated", "finance.TransactionPosted", ...]
    event_types = models.JSONField(default=list)
    is_active = models.BooleanField(default=True, db_index=True)
    last_delivery_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "integration_webhook_subscriptions"
        indexes = [
            models.Index(fields=["tenant_id", "is_active"], name="wh_sub_tenant_active_idx"),
        ]

    def __str__(self) -> str:
        return f"WebhookSubscription({self.url})"
