import uuid

import pytest
from django.test import Client

from apps.common.models.outbox import OutboxRecord
from apps.common.models.rbac import Permission, Role, RoleBinding, RolePermission
from apps.site.models.site_content import PagePublicationStatus, SitePageContent
from apps.users.models import User
from apps.users.services.auth_service import _make_access_token, _pk_to_uuid4

TENANT_ID = uuid.uuid4()
GW_TOKEN = "complect_internal_secure_mesh_token_2026"


@pytest.fixture(autouse=True)
def seed_cms_permissions(db):
    for action, resource in [("read", "cms"), ("write", "cms"), ("publish", "cms")]:
        Permission.objects.get_or_create(action=action, resource=resource)
    editor, _ = Role.objects.get_or_create(name="cms_editor")
    for action, resource in [("read", "cms"), ("write", "cms")]:
        perm = Permission.objects.get(action=action, resource=resource)
        RolePermission.objects.get_or_create(role=editor, permission=perm)


def _make_user_with_role(role_name: str) -> tuple[User, str]:
    email = f"{role_name}_{uuid.uuid4().hex[:8]}@test.com"
    user = User.objects.create_user(
        username=email, email=email, password="Pass1!", tenant_id=TENANT_ID
    )
    role, _ = Role.objects.get_or_create(name=role_name)
    RoleBinding.objects.create(
        tenant_id=TENANT_ID,
        user_id=uuid.UUID(_pk_to_uuid4(user.pk)),
        role=role,
    )
    return user, _make_access_token(user)


def _make_publisher() -> tuple[User, str]:
    """Publisher has cms.publish permission."""
    email = f"publisher_{uuid.uuid4().hex[:8]}@test.com"
    user = User.objects.create_user(
        username=email, email=email, password="Pass1!", tenant_id=TENANT_ID
    )
    role, _ = Role.objects.get_or_create(name="cms_publisher")
    pub_perm = Permission.objects.get(action="publish", resource="cms")
    RolePermission.objects.get_or_create(role=role, permission=pub_perm)
    read_perm = Permission.objects.get(action="read", resource="cms")
    RolePermission.objects.get_or_create(role=role, permission=read_perm)
    RoleBinding.objects.create(
        tenant_id=TENANT_ID,
        user_id=uuid.UUID(_pk_to_uuid4(user.pk)),
        role=role,
    )
    return user, _make_access_token(user)


def _post_publish(slug: str, user: User, token: str):
    return Client().post(
        f"/api/v1/cms/pages/{slug}/publish/",
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
        HTTP_X_GATEWAY_TOKEN=GW_TOKEN,
        HTTP_X_TENANT_ID=str(user.tenant_id),
        HTTP_X_USER_ID=_pk_to_uuid4(user.pk),
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )


def _create_draft_page(slug: str) -> SitePageContent:
    return SitePageContent.objects.create(
        tenant_id=TENANT_ID,
        slug=slug,
        status=PagePublicationStatus.DRAFT,
        content={"type": "doc", "content": []},
        meta={},
    )


@pytest.mark.django_db
class TestPublishEndpoint:
    def test_publish_draft_returns_200_and_status_published(self):
        _create_draft_page("home")
        user, token = _make_publisher()
        resp = _post_publish("home", user, token)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "PUBLISHED"
        assert data["slug"] == "home"

    def test_publish_creates_outbox_event(self):
        _create_draft_page("about")
        user, token = _make_publisher()
        outbox_count_before = OutboxRecord.objects.count()
        _post_publish("about", user, token)
        assert OutboxRecord.objects.count() == outbox_count_before + 1
        record = OutboxRecord.objects.order_by("-created_at").first()
        assert record.event_type == "cms.PagePublished"
        assert record.payload_json["page_slug"] == "about"

    def test_publish_sets_page_status_to_published_in_db(self):
        _create_draft_page("services")
        user, token = _make_publisher()
        _post_publish("services", user, token)
        page = SitePageContent.objects.get(tenant_id=TENANT_ID, slug="services")
        assert page.status == PagePublicationStatus.PUBLISHED

    def test_publish_nonexistent_page_returns_404(self):
        user, token = _make_publisher()
        resp = _post_publish("nonexistent", user, token)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "CMS_PAGE_NOT_FOUND"

    def test_publish_already_published_page_returns_409(self):
        page = _create_draft_page("contact")
        page.status = PagePublicationStatus.PUBLISHED
        page.save()
        user, token = _make_publisher()
        resp = _post_publish("contact", user, token)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "CMS_PAGE_ALREADY_PUBLISHED"

    def test_publish_requires_cms_publish_permission(self):
        from django.test import override_settings

        _create_draft_page("faq")
        user, token = _make_user_with_role("cms_editor")  # has read/write, not publish
        with override_settings(RBAC_ENFORCEMENT_MODE="ENFORCE"):
            resp = _post_publish("faq", user, token)
        assert resp.status_code == 403

    def test_publish_clears_publish_at_schedule(self):
        """Scheduled page becomes PUBLISHED and publish_at is cleared."""
        from datetime import datetime, timedelta, timezone

        page = SitePageContent.objects.create(
            tenant_id=TENANT_ID,
            slug="news",
            status=PagePublicationStatus.SCHEDULED,
            publish_at=datetime.now(tz=timezone.utc) + timedelta(days=1),
            content={"type": "doc"},
            meta={},
        )
        user, token = _make_publisher()
        resp = _post_publish("news", user, token)
        assert resp.status_code == 200
        page.refresh_from_db()
        assert page.status == PagePublicationStatus.PUBLISHED
        assert page.publish_at is None
