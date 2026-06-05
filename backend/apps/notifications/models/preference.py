from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class NotificationPreference(BaseEnterpriseModel):
    user_id = models.UUIDField()
    event_type = models.CharField(max_length=100)
    email_enabled = models.BooleanField(default=False)
    in_app_enabled = models.BooleanField(default=True)

    class Meta:
        db_table = "notifications_preferences"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "user_id", "event_type"],
                name="uq_notif_pref_user_event",
            )
        ]
