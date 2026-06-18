from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from django.db import transaction

from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.tasks import BaseEnterpriseTask
from apps.site.events.cms_events import PagePublishedEvent
from apps.site.models.site_content import PagePublicationStatus, SitePageContent
from config.celery import app

logger = structlog.get_logger(__name__)


@app.task(base=BaseEnterpriseTask, queue="default")
def publish_scheduled_pages_task() -> None:
    """
    Finds pages with status=SCHEDULED and publish_at <= now, transitions them to PUBLISHED,
    and emits PagePublishedEvent via Transactional Outbox for each.
    """
    now = datetime.now(tz=timezone.utc)
    published_count = 0

    pages = list(
        SitePageContent.objects.filter(
            status=PagePublicationStatus.SCHEDULED,
            publish_at__lte=now,
            is_deleted=False,
        ).select_for_update(skip_locked=True)[:50]
    )

    if not pages:
        return

    for page in pages:
        try:
            with transaction.atomic():
                page.status = PagePublicationStatus.PUBLISHED
                page.publish_at = None
                page.save(update_fields=["status", "publish_at", "updated_at"])

                event = PagePublishedEvent(
                    event_id=uuid.uuid4(),
                    event_version=1,
                    occurred_at=now,
                    correlation_id=uuid.uuid4(),
                    tenant_id=page.tenant_id,
                    page_slug=page.slug,
                )
                TransactionalOutboxService.push_to_outbox(event)
                published_count += 1
        except Exception:
            logger.exception(
                "publish_scheduled_page_failed",
                slug=page.slug,
                tenant_id=str(page.tenant_id),
            )

    if published_count:
        logger.info("scheduled_pages_published", count=published_count)
