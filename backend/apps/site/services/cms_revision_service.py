import uuid
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.site.models.site_content import SitePageContent
from apps.site.models.site_page_revision import SitePageRevision


class RevisionNotFoundError(Exception):
    pass


class PageNotFoundError(Exception):
    pass


class CmsRevisionService:
    MAX_REVISIONS = 20

    def create_revision(
        self,
        page: SitePageContent,
        created_by_id: uuid.UUID,
        label: str = "auto",
    ) -> SitePageRevision:
        with transaction.atomic():
            max_num = (
                SitePageRevision.objects
                .filter(tenant_id=page.tenant_id, page_id=page.id, is_deleted=False)
                .order_by("-revision_number")
                .values_list("revision_number", flat=True)
                .first()
            ) or 0
            new_number = max_num + 1

            revision = SitePageRevision.objects.create(
                tenant_id=page.tenant_id,
                page_id=page.id,
                revision_number=new_number,
                content=page.content,
                meta=page.meta,
                status=page.status or "",
                created_by_id=created_by_id,
                label=label,
            )

            count = SitePageRevision.objects.filter(
                tenant_id=page.tenant_id,
                page_id=page.id,
                is_deleted=False,
            ).count()

            if count > self.MAX_REVISIONS:
                oldest = (
                    SitePageRevision.objects
                    .filter(tenant_id=page.tenant_id, page_id=page.id, is_deleted=False)
                    .order_by("revision_number")
                    .first()
                )
                if oldest is not None:
                    oldest.delete()

        return revision

    def list_revisions(
        self,
        page_id: uuid.UUID,
        tenant_id: uuid.UUID,
        cursor: Optional[int] = None,
        limit: int = 20,
    ):
        qs = SitePageRevision.objects.filter(
            tenant_id=tenant_id,
            page_id=page_id,
            is_deleted=False,
        )
        if cursor is not None:
            qs = qs.filter(revision_number__lt=cursor)
        return qs.order_by("-revision_number")[: limit + 1]

    def rollback(
        self,
        page_id: uuid.UUID,
        revision_id: uuid.UUID,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> SitePageContent:
        try:
            revision = SitePageRevision.objects.get(
                id=revision_id,
                tenant_id=tenant_id,
                page_id=page_id,
                is_deleted=False,
            )
        except SitePageRevision.DoesNotExist:
            raise RevisionNotFoundError(f"Revision {revision_id} not found.") from None

        try:
            page = SitePageContent.objects.get(
                id=page_id,
                tenant_id=tenant_id,
                is_deleted=False,
            )
        except SitePageContent.DoesNotExist:
            raise PageNotFoundError(f"Page {page_id} not found.") from None

        with transaction.atomic():
            # Pre-rollback snapshot
            self.create_revision(page, user_id, label="pre-rollback snapshot")

            SitePageContent.objects.filter(
                id=page_id,
                tenant_id=tenant_id,
            ).update(
                content=revision.content,
                meta=revision.meta,
                status=revision.status or None,
                updated_at=timezone.now(),
            )

        page.refresh_from_db()

        # Post-rollback revision recording the restored state
        self.create_revision(
            page,
            user_id,
            label=f"rollback from #{revision.revision_number}",
        )

        return page
