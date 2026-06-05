import uuid

from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class InAppNotification(BaseEnterpriseModel):
    user_id = models.UUIDField()
    title = models.CharField(max_length=255)
    body = models.TextField()
    event_type = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    read_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "notifications_in_app"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["tenant_id", "user_id", "-created_at"],
                name="notif_inapp_tenant_user_idx",
            ),
            models.Index(
                fields=["tenant_id", "user_id", "read_at"],
                name="notif_inapp_unread_idx",
            ),
        ]
