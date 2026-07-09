import uuid
from datetime import datetime, timedelta, timezone

import pytest

from apps.common.models.outbox import OutboxRecord
from apps.site.models.site_content import PagePublicationStatus, SitePageContent
from apps.site.tasks.publisher import publish_scheduled_pages_task

TENANT_ID = uuid.uuid4()


def _make_page(slug: str, status: str, publish_at: datetime | None = None) -> SitePageContent:
    return SitePageContent.objects.create(
        tenant_id=TENANT_ID,
        slug=slug,
        status=status,
        publish_at=publish_at,
        content={"type": "doc"},
        meta={},
    )


@pytest.mark.django_db
class TestPublishScheduledPagesTask:
    def test_publishes_overdue_scheduled_pages(self):
        past = datetime.now(tz=timezone.utc) - timedelta(minutes=10)
        _make_page("news-1", PagePublicationStatus.SCHEDULED, publish_at=past)
        _make_page("news-2", PagePublicationStatus.SCHEDULED, publish_at=past)

        publish_scheduled_pages_task()

        for slug in ("news-1", "news-2"):
            page = SitePageContent.objects.get(tenant_id=TENANT_ID, slug=slug)
            assert page.status == PagePublicationStatus.PUBLISHED
            assert page.publish_at is None

    def test_does_not_publish_future_scheduled_pages(self):
        future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        _make_page("future-post", PagePublicationStatus.SCHEDULED, publish_at=future)

        publish_scheduled_pages_task()

        page = SitePageContent.objects.get(tenant_id=TENANT_ID, slug="future-post")
        assert page.status == PagePublicationStatus.SCHEDULED

    def test_does_not_touch_draft_pages(self):
        _make_page("draft-page", PagePublicationStatus.DRAFT)

        publish_scheduled_pages_task()

        page = SitePageContent.objects.get(tenant_id=TENANT_ID, slug="draft-page")
        assert page.status == PagePublicationStatus.DRAFT

    def test_emits_outbox_event_per_page(self):
        past = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
        _make_page("event-page-1", PagePublicationStatus.SCHEDULED, publish_at=past)
        _make_page("event-page-2", PagePublicationStatus.SCHEDULED, publish_at=past)

        before = OutboxRecord.objects.count()
        publish_scheduled_pages_task()

        assert OutboxRecord.objects.count() == before + 2
        events = OutboxRecord.objects.filter(event_type="cms.PagePublished").order_by("created_at")
        slugs = {e.payload_json["page_slug"] for e in events}
        assert "event-page-1" in slugs
        assert "event-page-2" in slugs

    def test_task_registered_in_beat_schedule(self):
        from django.conf import settings

        assert "cms-publish-scheduled" in settings.CELERY_BEAT_SCHEDULE
        entry = settings.CELERY_BEAT_SCHEDULE["cms-publish-scheduled"]
        assert entry["task"] == "apps.site.tasks.publisher.publish_scheduled_pages_task"
        assert entry["schedule"] == 300.0
        assert entry["options"]["queue"] == "default"
