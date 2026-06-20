# backend/apps/site/tests/test_cms_api.py
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
    viewer, _ = Role.objects.get_or_create(name="cms_viewer")

    for action, resource in [("read", "cms"), ("write", "cms")]:
        perm = Permission.objects.get(action=action, resource=resource)
        RolePermission.objects.get_or_create(role=editor, permission=perm)

    perm = Permission.objects.get(action="read", resource="cms")
    RolePermission.objects.get_or_create(role=viewer, permission=perm)


def _make_user_with_role(role_name: str) -> tuple[User, str]:
    email = f"{role_name}_{uuid.uuid4().hex[:8]}@test.com"
    user = User.objects.create_user(
        username=email, email=email, password="Pass1!", tenant_id=TENANT_ID
    )
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


@pytest.mark.django_db
class TestGetCmsPage:
    def test_editor_can_read_page(self):
        """cms_editor with cms.read can GET existing page."""
        from apps.site.models import SitePageContent

        SitePageContent.objects.create(
            tenant_id=TENANT_ID,
            slug="home",
            content={"type": "doc"},
            meta={"title": "Главная"},
        )

        user, token = _make_user_with_role("cms_editor")
        resp = Client().get("/api/v1/cms/pages/home/", **_auth_headers(user, token))

        assert resp.status_code == 200
        data = resp.json()
        assert data["slug"] == "home"
        assert data["content"]["type"] == "doc"

    def test_returns_404_for_missing_page(self):
        user, token = _make_user_with_role("cms_editor")
        resp = Client().get("/api/v1/cms/pages/nonexistent/", **_auth_headers(user, token))
        assert resp.status_code == 404


@pytest.mark.django_db
class TestPatchCmsPage:
    def test_editor_can_update_page(self):
        """AE-7: PATCH updates page, GET /public returns new content."""
        from apps.site.models import SitePageContent

        SitePageContent.objects.create(
            tenant_id=TENANT_ID,
            slug="about",
            content={"type": "doc", "content": []},
            meta={},
        )

        user, token = _make_user_with_role("cms_editor")
        new_content = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]}],
        }
        resp = Client().patch(
            "/api/v1/cms/pages/about/",
            data=json.dumps({"content": new_content}),
            content_type="application/json",
            **_auth_headers(user, token, mutating=True),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["content"]["content"][0]["type"] == "paragraph"

    def test_upsert_creates_page_when_missing(self):
        """PATCH on non-existent slug creates it (upsert)."""
        user, token = _make_user_with_role("cms_editor")
        resp = Client().patch(
            "/api/v1/cms/pages/new-slug/",
            data=json.dumps({"content": {"type": "doc"}, "meta": {"title": "New"}}),
            content_type="application/json",
            **_auth_headers(user, token, mutating=True),
        )
        assert resp.status_code == 200
        assert resp.json()["slug"] == "new-slug"

    def test_viewer_cannot_write(self):
        """AE-8: cms_viewer without cms.write gets 403 on PATCH (RBAC ENFORCE mode)."""
        from django.test import override_settings

        user, token = _make_user_with_role("cms_viewer")
        with override_settings(RBAC_ENFORCEMENT_MODE="ENFORCE"):
            resp = Client().patch(
                "/api/v1/cms/pages/about/",
                data=json.dumps({"content": {"type": "doc", "content": []}}),
                content_type="application/json",
                **_auth_headers(user, token, mutating=True),
            )
        assert resp.status_code == 403


@pytest.mark.django_db
class TestCmsGallery:
    def test_list_gallery_empty(self):
        user, token = _make_user_with_role("cms_editor")
        resp = Client().get("/api/v1/cms/gallery/", **_auth_headers(user, token))
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_create_gallery_image(self):
        """AE-9: POST gallery creates SiteImage."""
        user, token = _make_user_with_role("cms_editor")
        resp = Client().post(
            "/api/v1/cms/gallery/?section=banner&s3_key=uploads/banner1.jpg",
            **_auth_headers(user, token, mutating=True),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["section"] == "banner"
        assert data["s3_key"] == "uploads/banner1.jpg"


@pytest.mark.django_db
class TestGalleryCRUD:
    """AE-U1: Gallery CRUD endpoints."""

    def _create_image(self, user):
        from apps.site.models import SiteImage

        return SiteImage.objects.create(
            tenant_id=user.tenant_id,
            section="banner",
            s3_key="cms/gallery/test/img.jpg",
            caption="Original",
            alt="Original alt",
            order=0,
        )

    # ── DELETE ─────────────────────────────────────────────────────────────────

    def test_delete_gallery_image_success(self):
        """DELETE /gallery/{id}/ with write:cms → 204."""
        user, token = _make_user_with_role("cms_editor")
        img = self._create_image(user)
        resp = Client().delete(
            f"/api/v1/cms/gallery/{img.id}/",
            **_auth_headers(user, token, mutating=True),
        )
        assert resp.status_code == 204

        from apps.site.models import SiteImage

        assert SiteImage.objects.get(pk=img.pk).is_deleted is True

    def test_delete_gallery_image_wrong_tenant(self):
        """DELETE another tenant's image → 404."""
        from apps.site.models import SiteImage

        other_tenant_id = uuid.uuid4()
        img = SiteImage.objects.create(
            tenant_id=other_tenant_id,
            section="hero",
            s3_key="cms/gallery/other/img.jpg",
            caption="",
            alt="",
            order=0,
        )
        user, token = _make_user_with_role("cms_editor")
        resp = Client().delete(
            f"/api/v1/cms/gallery/{img.id}/",
            **_auth_headers(user, token, mutating=True),
        )
        assert resp.status_code == 404

    def test_delete_gallery_image_no_permission(self):
        """DELETE without write:cms (cms_viewer) → 403."""
        from django.test import override_settings

        user, token = _make_user_with_role("cms_viewer")
        img = self._create_image(user)
        with override_settings(RBAC_ENFORCEMENT_MODE="ENFORCE"):
            resp = Client().delete(
                f"/api/v1/cms/gallery/{img.id}/",
                **_auth_headers(user, token, mutating=True),
            )
        assert resp.status_code == 403

    # ── PATCH ──────────────────────────────────────────────────────────────────

    def test_patch_gallery_image_updates_caption(self):
        """PATCH /gallery/{id}/ with {"caption": "new"} → 200, caption updated."""
        import json

        user, token = _make_user_with_role("cms_editor")
        img = self._create_image(user)
        resp = Client().patch(
            f"/api/v1/cms/gallery/{img.id}/",
            data=json.dumps({"caption": "Updated caption"}),
            content_type="application/json",
            **_auth_headers(user, token, mutating=True),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["caption"] == "Updated caption"
        assert data["alt"] == "Original alt"  # unchanged

        from apps.site.models import SiteImage

        assert SiteImage.objects.get(pk=img.pk).caption == "Updated caption"

    # ── REORDER ────────────────────────────────────────────────────────────────

    def test_reorder_gallery_images(self):
        """POST /gallery/reorder/ → updated order persists."""
        import json

        user, token = _make_user_with_role("cms_editor")

        from apps.site.models import SiteImage

        img_a = SiteImage.objects.create(
            tenant_id=user.tenant_id, section="hero", s3_key="a.jpg", caption="", alt="", order=0,
        )
        img_b = SiteImage.objects.create(
            tenant_id=user.tenant_id, section="hero", s3_key="b.jpg", caption="", alt="", order=1,
        )

        resp = Client().post(
            "/api/v1/cms/gallery/reorder/",
            data=json.dumps({"items": [
                {"id": str(img_a.id), "order": 10},
                {"id": str(img_b.id), "order": 5},
            ]}),
            content_type="application/json",
            **_auth_headers(user, token, mutating=True),
        )
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2

        assert SiteImage.objects.get(pk=img_a.pk).order == 10
        assert SiteImage.objects.get(pk=img_b.pk).order == 5

    # ── CONFIRM UPLOAD ─────────────────────────────────────────────────────────

    def test_confirm_upload_creates_image(self):
        """POST /gallery/confirm-upload/ with valid payload → 201, SiteImage created."""
        import json

        user, token = _make_user_with_role("cms_editor")
        resp = Client().post(
            "/api/v1/cms/gallery/confirm-upload/",
            data=json.dumps({
                "s3_key": "cms/gallery/abc/photo.jpg",
                "section": "team",
                "caption": "Team photo",
                "alt": "Our team",
                "order": 2,
            }),
            content_type="application/json",
            **_auth_headers(user, token, mutating=True),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["s3_key"] == "cms/gallery/abc/photo.jpg"
        assert data["section"] == "team"
        assert data["caption"] == "Team photo"
        assert data["alt"] == "Our team"

        from apps.site.models import SiteImage

        assert SiteImage.objects.filter(tenant_id=user.tenant_id, s3_key="cms/gallery/abc/photo.jpg").exists()

    # ── REQUEST UPLOAD ─────────────────────────────────────────────────────────

    def test_request_upload_returns_presigned_url(self):
        """POST /gallery/request-upload/ → PresignedUploadResponse with upload_url and s3_key."""
        from unittest.mock import MagicMock, patch

        user, token = _make_user_with_role("cms_editor")

        mock_backend = MagicMock()
        mock_backend.generate_upload_url.return_value = "https://s3.example.com/presigned-put"

        with patch("apps.site.services.cms_services.get_storage_backend", return_value=mock_backend):
            resp = Client().post(
                "/api/v1/cms/gallery/request-upload/?filename=photo.jpg&content_type=image%2Fjpeg&size_bytes=102400",
                **_auth_headers(user, token, mutating=True),
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "upload_url" in data
        assert "s3_key" in data
        assert data["upload_url"] == "https://s3.example.com/presigned-put"
        assert data["s3_key"].startswith("cms/gallery/")
        assert data["s3_key"].endswith("/photo.jpg")
        assert data["expires_in"] == 3600
