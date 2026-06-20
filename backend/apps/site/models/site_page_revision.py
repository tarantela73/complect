from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class SitePageRevision(BaseEnterpriseModel):
    class Meta:
        db_table = "site_page_revisions"
        indexes = [
            models.Index(
                fields=["tenant_id", "page_id", "revision_number"],
                name="idx_revision_tenant_page_num",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "page_id", "revision_number"],
                name="uniq_revision_per_page",
            )
        ]

    page_id = models.UUIDField(db_index=True)
    revision_number = models.PositiveIntegerField()
    content = models.JSONField()
    meta = models.JSONField()
    status = models.CharField(max_length=20, blank=True, default="")
    created_by_id = models.UUIDField()
    label = models.CharField(max_length=200, blank=True, default="")
