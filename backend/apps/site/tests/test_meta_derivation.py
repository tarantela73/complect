import json
import uuid

import pytest
from django.test import Client

from apps.common.models.rbac import Permission, Role, RoleBinding, RolePermission
from apps.site.services.cms_update_service import _derive_meta_from_tiptap
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


def _make_editor() -> tuple[User, str]:
    email = f"editor_{uuid.uuid4().hex[:8]}@test.com"
    user = User.objects.create_user(username=email, email=email, password="Pass1!", tenant_id=TENANT_ID)
    role = Role.objects.get(name="cms_editor")
    RoleBinding.objects.create(
        tenant_id=TENANT_ID,
        user_id=uuid.UUID(_pk_to_uuid4(user.pk)),
        role=role,
    )
    return user, _make_access_token(user)


def _patch(slug: str, user: User, token: str, body: dict) -> dict:
    resp = Client().patch(
        f"/api/v1/cms/pages/{slug}/",
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
        HTTP_X_GATEWAY_TOKEN=GW_TOKEN,
        HTTP_X_TENANT_ID=str(user.tenant_id),
        HTTP_X_USER_ID=_pk_to_uuid4(user.pk),
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    return resp


# ── _derive_meta_from_tiptap unit tests ──────────────────────────────────────


class TestDeriveMetaFromTiptap:
    def test_derives_title_from_first_heading(self):
        doc = {
            "type": "doc",
            "content": [
                {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Услуги"}]},
            ],
        }
        result = _derive_meta_from_tiptap(doc)
        assert result["title"] == "Услуги"

    def test_derives_description_from_first_paragraph(self):
        doc = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Мы помогаем."}]},
            ],
        }
        result = _derive_meta_from_tiptap(doc)
        assert result["description"] == "Мы помогаем."

    def test_derives_both_from_doc(self):
        doc = {
            "type": "doc",
            "content": [
                {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Заголовок"}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "Описание страницы."}]},
            ],
        }
        result = _derive_meta_from_tiptap(doc)
        assert result["title"] == "Заголовок"
        assert result["description"] == "Описание страницы."

    def test_returns_empty_when_no_heading_or_paragraph(self):
        doc = {
            "type": "doc",
            "content": [
                {"type": "bulletList", "content": []},
            ],
        }
        result = _derive_meta_from_tiptap(doc)
        assert result == {}

    def test_truncates_long_title_to_60_chars(self):
        long_title = "A" * 80
        doc = {
            "type": "doc",
            "content": [
                {"type": "heading", "content": [{"type": "text", "text": long_title}]},
            ],
        }
        result = _derive_meta_from_tiptap(doc)
        assert len(result["title"]) == 60

    def test_truncates_long_description_to_160_chars(self):
        long_desc = "B" * 200
        doc = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": long_desc}]},
            ],
        }
        result = _derive_meta_from_tiptap(doc)
        assert len(result["description"]) == 160

    def test_handles_empty_content(self):
        assert _derive_meta_from_tiptap({}) == {}
        assert _derive_meta_from_tiptap({"type": "doc"}) == {}


# ── _overrides integration tests ─────────────────────────────────────────────


@pytest.mark.django_db
class TestMetaAutoDerivation:
    def test_auto_derives_title_and_description_on_content_save(self):
        """AE5: when no override, content save populates title and description."""
        user, token = _make_editor()
        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 1},
                    "content": [{"type": "text", "text": "Подключение электричества"}],
                },
                {"type": "paragraph", "content": [{"type": "text", "text": "Мы помогаем с подключением."}]},
            ],
        }
        resp = _patch("services", user, token, {"content": doc})
        assert resp.status_code == 200
        meta = resp.json()["meta"]
        assert meta["title"] == "Подключение электричества"
        assert meta["description"] == "Мы помогаем с подключением."
        assert meta.get("_overrides", {}).get("title") is not True

    def test_explicit_meta_sets_override_flag(self):
        """Setting title via meta marks _overrides.title = True."""
        user, token = _make_editor()
        resp = _patch("about", user, token, {"meta": {"title": "Услуги компании | Комплект"}})
        assert resp.status_code == 200
        meta = resp.json()["meta"]
        assert meta["title"] == "Услуги компании | Комплект"
        assert meta["_overrides"]["title"] is True

    def test_override_protects_title_on_subsequent_content_save(self):
        """AE3: overridden title stays on next content save with new H1."""
        user, token = _make_editor()
        # Set custom title — marks override
        _patch("contact", user, token, {"meta": {"title": "Услуги компании | Комплект"}})
        # Save new content with different H1
        doc = {
            "type": "doc",
            "content": [
                {"type": "heading", "content": [{"type": "text", "text": "Новый заголовок"}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "Новое описание."}]},
            ],
        }
        resp = _patch("contact", user, token, {"content": doc})
        assert resp.status_code == 200
        meta = resp.json()["meta"]
        assert meta["title"] == "Услуги компании | Комплект"  # protected
        assert meta["description"] == "Новое описание."  # auto-derived

    def test_unprotected_field_updates_on_content_save(self):
        """Only _overrides.description = True blocks description derivation."""
        user, token = _make_editor()
        _patch("faq", user, token, {"meta": {"description": "Зафиксированное описание."}})
        doc = {
            "type": "doc",
            "content": [
                {"type": "heading", "content": [{"type": "text", "text": "Новый заголовок"}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "Другое описание."}]},
            ],
        }
        resp = _patch("faq", user, token, {"content": doc})
        meta = resp.json()["meta"]
        assert meta["title"] == "Новый заголовок"  # not protected, auto-derived
        assert meta["description"] == "Зафиксированное описание."  # protected


# ── MetaUpdateRequest validation ─────────────────────────────────────────────


@pytest.mark.django_db
class TestMetaUpdateRequestValidation:
    def test_rejects_title_over_60_chars(self):
        user, token = _make_editor()
        resp = _patch("home", user, token, {"meta": {"title": "A" * 61}})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_rejects_description_over_160_chars(self):
        user, token = _make_editor()
        resp = _patch("home", user, token, {"meta": {"description": "B" * 161}})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_accepts_title_at_exactly_60_chars(self):
        user, token = _make_editor()
        resp = _patch("home", user, token, {"meta": {"title": "A" * 60}})
        assert resp.status_code == 200
