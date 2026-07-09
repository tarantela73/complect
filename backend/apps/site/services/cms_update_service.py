import uuid

from django.db import transaction
from django.utils import timezone

from apps.common.context.core import get_tenant_id, get_user_id
from apps.site.models import SiteImage, SitePageContent
from apps.site.schemas.meta_schema import MetaUpdateRequest


def _derive_meta_from_tiptap(content: dict) -> dict:
    """Extract title from first heading node and description from first paragraph node."""
    derived: dict = {}
    nodes = content.get("content", []) if isinstance(content, dict) else []

    def _text(node: dict) -> str:
        if node.get("type") == "text":
            return node.get("text", "")
        return "".join(_text(child) for child in node.get("content", []))

    for node in nodes:
        node_type = node.get("type")
        if node_type == "heading" and "title" not in derived:
            text = _text(node).strip()
            if text:
                derived["title"] = text[:60]
        elif node_type == "paragraph" and "description" not in derived:
            text = _text(node).strip()
            if text:
                derived["description"] = text[:160]
        if len(derived) == 2:
            break

    return derived


class CmsUpdateService:
    @staticmethod
    def upsert_page(
        slug: str,
        content: dict | None,
        meta_update: MetaUpdateRequest | None = None,
    ) -> SitePageContent:
        from apps.site.services.cms_revision_service import CmsRevisionService

        tenant_id = get_tenant_id()

        try:
            user_id = get_user_id()
        except Exception:
            user_id = None

        existing = SitePageContent.objects.filter(tenant_id=tenant_id, slug=slug, is_deleted=False).first()

        current_meta: dict = dict(existing.meta) if existing else {}
        overrides: dict = dict(current_meta.pop("_overrides", {}))

        if content is not None:
            for field, value in _derive_meta_from_tiptap(content).items():
                if not overrides.get(field):
                    current_meta[field] = value

        if meta_update is not None:
            for field, value in meta_update.model_dump(exclude_none=True).items():
                current_meta[field] = value
                overrides[field] = True

        current_meta["_overrides"] = overrides

        defaults: dict = {"meta": current_meta}
        if content is not None:
            defaults["content"] = content

        if existing is None:
            with transaction.atomic():
                page = SitePageContent.objects.create(
                    tenant_id=tenant_id,
                    slug=slug,
                    **defaults,
                )
        else:
            with transaction.atomic():
                revision_svc = CmsRevisionService()
                revision_svc.create_revision(
                    existing,
                    created_by_id=user_id or uuid.UUID(int=0),
                )
                SitePageContent.objects.filter(
                    pk=existing.pk,
                    tenant_id=tenant_id,
                ).update(
                    updated_at=timezone.now(),
                    **defaults,
                )
            existing.refresh_from_db()
            page = existing

        return page

    @staticmethod
    def create_image(section: str, s3_key: str, caption: str = "", order: int = 0) -> SiteImage:
        tenant_id = get_tenant_id()
        img = SiteImage(
            tenant_id=tenant_id,
            section=section,
            s3_key=s3_key,
            caption=caption,
            order=order,
        )
        img.save()
        return img
