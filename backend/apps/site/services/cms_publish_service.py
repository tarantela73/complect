from __future__ import annotations

import uuid
from datetime import datetime, timezone

from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist

from apps.common.context.core import get_correlation_id, get_tenant_id
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.site.events.cms_events import PagePublishedEvent
from apps.site.models.site_content import PagePublicationStatus, SitePageContent


class PageNotFoundError(Exception):
    pass


class PageAlreadyPublishedError(Exception):
    pass


class CmsPublishService:
    @staticmethod
    def publish_page(slug: str) -> SitePageContent:
        """
        Transitions a page from DRAFT/SCHEDULED → PUBLISHED and emits PagePublishedEvent.

        Raises:
            PageNotFoundError: page does not exist for this tenant.
            PageAlreadyPublishedError: page status is already PUBLISHED.
        """
        tenant_id = get_tenant_id()
        correlation_id = get_correlation_id()

        with transaction.atomic():
            try:
                page = (
                    SitePageContent.objects
                    .select_for_update()
                    .get(tenant_id=tenant_id, slug=slug, is_deleted=False)
                )
            except SitePageContent.DoesNotExist as exc:
                raise PageNotFoundError(slug) from exc

            if page.status == PagePublicationStatus.PUBLISHED:
                raise PageAlreadyPublishedError(slug)

            page.status = PagePublicationStatus.PUBLISHED
            page.publish_at = None
            page.save(update_fields=["status", "publish_at", "updated_at"])

            event = PagePublishedEvent(
                event_id=uuid.uuid4(),
                event_version=1,
                occurred_at=datetime.now(tz=timezone.utc),
                correlation_id=correlation_id,
                tenant_id=tenant_id,
                page_slug=slug,
            )
            TransactionalOutboxService.push_to_outbox(event)

        return page
