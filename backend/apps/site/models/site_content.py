from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class PagePublicationStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    PUBLISHED = "PUBLISHED", "Published"
    SCHEDULED = "SCHEDULED", "Scheduled"


class SitePageContent(BaseEnterpriseModel):
    slug = models.CharField(max_length=50, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=PagePublicationStatus.choices,
        null=True,
        blank=True,
        db_index=True,
    )
    publish_at = models.DateTimeField(null=True, blank=True)
    content = models.JSONField(default=dict)
    meta = models.JSONField(default=dict)

    class Meta:
        db_table = "site_page_content"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "slug"],
                condition=models.Q(is_deleted=False),
                name="unique_site_page_content_tenant_slug",
            )
        ]


class SiteImage(BaseEnterpriseModel):
    section = models.CharField(max_length=50, db_index=True)
    s3_key = models.CharField(max_length=500)
    caption = models.CharField(max_length=255, blank=True, default="")
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "site_images"
        ordering = ["order", "created_at"]
