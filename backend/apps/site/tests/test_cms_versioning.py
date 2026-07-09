# backend/apps/site/tests/test_cms_versioning.py
import json
import uuid

import pytest
from django.test import Client

from apps.common.models.rbac import Permission, Role, RoleBinding, RolePermission
from apps.users.models import User
from apps.users.services.auth_service import _make_access_token, _pk_to_uuid4

TENANT_ID = uuid.uuid4()
GW_TOKEN = "complect_internal_secure_mesh_token_2026"


@pytest.fixture(autouse=True)
def seed_cms_permissions(db):
    for action, resource in [("read", "cms"), ("write", "cms")]:
        Permission.objects.get_or_create(action=action, resource=resource)

    editor, _ = Role.objects.get_or_create(name="cms_editor")

    for action, resource in [("read", "cms"), ("write", "cms")]:
        perm = Permission.objects.get(action=action, resource=resource)
        RolePermission.objects.get_or_create(role=editor, permission=perm)


def _make_user_with_role(role_name: str) -> tuple[User, str]:
    email = f"{role_name}_{uuid.uuid4().hex[:8]}@test.com"
    user = User.objects.create_user(username=email, email=email, password="Pass1!", tenant_id=TENANT_ID)
    role = Role.objects.get(name=role_name)
    RoleBinding.objects.create(
        tenant_id=TENANT_ID,
        user_id=uuid.UUID(_pk_to_uuid4(user.pk)),
        role=role,
    )
    return user, _make_access_token(user)


def _auth_headers(user: User, token: str, mutating: bool = False) -> dict:
    h = {
        "HTTP_AUTHORIZATION": f"Bearer {token}",
        "HTTP_X_GATEWAY_TOKEN": GW_TOKEN,
        "HTTP_X_TENANT_ID": str(user.tenant_id),
        "HTTP_X_USER_ID": _pk_to_uuid4(user.pk),
    }
    if mutating:
        h["HTTP_IDEMPOTENCY_KEY"] = str(uuid.uuid4())
    return h


def _create_page(slug: str = "test-page"):
    from apps.site.models import SitePageContent

    return SitePageContent.objects.create(
        tenant_id=TENANT_ID,
        slug=slug,
        content={"type": "doc", "content": []},
        meta={"title": "Initial"},
        status="DRAFT",
    )


@pytest.mark.django_db
class TestPatchCreatesRevision:
    def test_patch_creates_revision(self):
        from apps.site.models import SitePageRevision

        _create_page("rev-test-1")
        user, token = _make_user_with_role("cms_editor")
        Client().patch(
            "/api/v1/cms/pages/rev-test-1/",
            data=json.dumps({"content": {"type": "doc", "content": []}}),
            content_type="application/json",
            **_auth_headers(user, token, mutating=True),
        )

        revisions = list(SitePageRevision.objects.filter(tenant_id=TENANT_ID, is_deleted=False))
        assert len(revisions) == 1
        assert revisions[0].revision_number == 1

    def test_patch_increments_revision_number(self):
        from apps.site.models import SitePageRevision

        _create_page("rev-test-2")
        user, token = _make_user_with_role("cms_editor")

        for i in range(2):
            Client().patch(
                "/api/v1/cms/pages/rev-test-2/",
                data=json.dumps({"content": {"type": "doc", "content": [{"type": "paragraph", "text": str(i)}]}}),
                content_type="application/json",
                **_auth_headers(user, token, mutating=True),
            )

        revisions = list(
            SitePageRevision.objects.filter(tenant_id=TENANT_ID, is_deleted=False).order_by("revision_number")
        )
        assert len(revisions) == 2
        assert revisions[0].revision_number == 1
        assert revisions[1].revision_number == 2

    def test_revision_limit_20(self):
        from apps.site.models import SitePageRevision

        _create_page("rev-limit")
        user, token = _make_user_with_role("cms_editor")

        for i in range(21):
            Client().patch(
                "/api/v1/cms/pages/rev-limit/",
                data=json.dumps({"content": {"type": "doc", "content": [{"type": "text", "text": str(i)}]}}),
                content_type="application/json",
                **_auth_headers(user, token, mutating=True),
            )

        count = SitePageRevision.objects.filter(tenant_id=TENANT_ID, is_deleted=False).count()
        assert count == 20


@pytest.mark.django_db
class TestListRevisions:
    def test_list_revisions(self):
        _create_page("rev-list")
        user, token = _make_user_with_role("cms_editor")

        for i in range(3):
            Client().patch(
                "/api/v1/cms/pages/rev-list/",
                data=json.dumps({"content": {"type": "doc", "content": [{"type": "text", "text": str(i)}]}}),
                content_type="application/json",
                **_auth_headers(user, token, mutating=True),
            )

        resp = Client().get(
            "/api/v1/cms/pages/rev-list/revisions/",
            **_auth_headers(user, token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        numbers = [r["revision_number"] for r in data["results"]]
        # Sorted DESC
        assert numbers == sorted(numbers, reverse=True)
        assert len(numbers) == 3


@pytest.mark.django_db
class TestRollback:
    def test_rollback_restores_content(self):
        from apps.site.models import SitePageRevision

        page = _create_page("rev-rollback")
        original_content = dict(page.content)

        user, token = _make_user_with_role("cms_editor")
        new_content = {"type": "doc", "content": [{"type": "paragraph", "text": "updated"}]}
        Client().patch(
            "/api/v1/cms/pages/rev-rollback/",
            data=json.dumps({"content": new_content}),
            content_type="application/json",
            **_auth_headers(user, token, mutating=True),
        )

        rev1 = (
            SitePageRevision.objects.filter(tenant_id=TENANT_ID, is_deleted=False).order_by("revision_number").first()
        )
        assert rev1 is not None

        resp = Client().post(
            f"/api/v1/cms/pages/rev-rollback/revisions/{rev1.id}/rollback/",
            **_auth_headers(user, token, mutating=True),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == original_content

    def test_rollback_creates_new_revision(self):
        from apps.site.models import SitePageRevision

        _create_page("rev-rollback-label")
        user, token = _make_user_with_role("cms_editor")

        Client().patch(
            "/api/v1/cms/pages/rev-rollback-label/",
            data=json.dumps({"content": {"type": "doc", "content": []}}),
            content_type="application/json",
            **_auth_headers(user, token, mutating=True),
        )

        rev1 = (
            SitePageRevision.objects.filter(tenant_id=TENANT_ID, is_deleted=False).order_by("revision_number").first()
        )

        before_count = SitePageRevision.objects.filter(tenant_id=TENANT_ID, is_deleted=False).count()

        Client().post(
            f"/api/v1/cms/pages/rev-rollback-label/revisions/{rev1.id}/rollback/",
            **_auth_headers(user, token, mutating=True),
        )

        after_count = SitePageRevision.objects.filter(tenant_id=TENANT_ID, is_deleted=False).count()
        assert after_count > before_count

        rollback_rev = (
            SitePageRevision.objects.filter(tenant_id=TENANT_ID, is_deleted=False).order_by("-revision_number").first()
        )
        assert "rollback" in rollback_rev.label

    def test_rollback_wrong_tenant(self):

        page = _create_page("rev-wrong-tenant")
        # Create revision for this page under TENANT_ID
        from apps.site.services.cms_revision_service import CmsRevisionService

        rev = CmsRevisionService().create_revision(page, uuid.uuid4())

        # Create another tenant's user
        other_tenant = uuid.uuid4()
        email = f"other_{uuid.uuid4().hex[:8]}@test.com"
        other_user = User.objects.create_user(username=email, email=email, password="Pass1!", tenant_id=other_tenant)
        role = Role.objects.get(name="cms_editor")
        RoleBinding.objects.create(
            tenant_id=other_tenant,
            user_id=uuid.UUID(_pk_to_uuid4(other_user.pk)),
            role=role,
        )
        token = _make_access_token(other_user)

        # Create a page for the other tenant with same slug
        from apps.site.models import SitePageContent

        SitePageContent.objects.create(
            tenant_id=other_tenant,
            slug="rev-wrong-tenant",
            content={"type": "doc"},
            meta={},
            status="DRAFT",
        )

        resp = Client().post(
            f"/api/v1/cms/pages/rev-wrong-tenant/revisions/{rev.id}/rollback/",
            **{
                "HTTP_AUTHORIZATION": f"Bearer {token}",
                "HTTP_X_GATEWAY_TOKEN": GW_TOKEN,
                "HTTP_X_TENANT_ID": str(other_tenant),
                "HTTP_X_USER_ID": _pk_to_uuid4(other_user.pk),
                "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 404

    def test_rollback_nonexistent_revision(self):
        _create_page("rev-nonexistent")
        user, token = _make_user_with_role("cms_editor")

        fake_id = uuid.uuid4()
        resp = Client().post(
            f"/api/v1/cms/pages/rev-nonexistent/revisions/{fake_id}/rollback/",
            **_auth_headers(user, token, mutating=True),
        )
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"]["code"] == "REVISION_NOT_FOUND"
