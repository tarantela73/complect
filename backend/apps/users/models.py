
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    tenant_id = models.UUIDField(db_index=True, null=True, blank=True)

    mfa_enabled = models.BooleanField(default=False)
    mfa_secret = models.CharField(max_length=64, blank=True, default="")

    device_fingerprint = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        db_table = "users_users"
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"
        indexes = [
            models.Index(fields=["tenant_id"], name="idx_users_tenant"),
            models.Index(fields=["tenant_id", "username"], name="idx_users_tenant_username"),
        ]
