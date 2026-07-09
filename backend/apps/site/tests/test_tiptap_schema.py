import json
import uuid

import pytest
from django.test import Client
from pydantic import ValidationError

from apps.common.models.rbac import Permission, Role, RoleBinding, RolePermission
from apps.site.schemas.tiptap_schema import TiptapDocSchema
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


def _patch(slug: str, user: User, token: str, body: dict):
    return Client().patch(
        f"/api/v1/cms/pages/{slug}/",
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
        HTTP_X_GATEWAY_TOKEN=GW_TOKEN,
        HTTP_X_TENANT_ID=str(user.tenant_id),
        HTTP_X_USER_ID=_pk_to_uuid4(user.pk),
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )


# ── TiptapDocSchema unit tests ────────────────────────────────────────────────


class TestTiptapDocSchema:
    def test_accepts_valid_doc(self):
        doc = TiptapDocSchema(
            type="doc",
            content=[
                {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Hello"}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "World"}]},
            ],
        )
        assert doc.type == "doc"
        assert len(doc.content) == 2

    def test_accepts_all_allowed_node_types(self):
        doc = TiptapDocSchema(
            type="doc",
            content=[
                {"type": "paragraph"},
                {"type": "heading"},
                {"type": "bulletList"},
                {"type": "orderedList"},
                {"type": "blockquote"},
                {"type": "codeBlock"},
                {"type": "hardBreak"},
                {"type": "horizontalRule"},
                {"type": "image"},
            ],
        )
        assert len(doc.content) == 9

    def test_rejects_unknown_node_type_at_root(self):
        with pytest.raises(ValidationError) as exc_info:
            TiptapDocSchema(
                type="doc",
                content=[
                    {"type": "script"},
                ],
            )
        assert "Unknown node type" in str(exc_info.value)

    def test_rejects_unknown_node_type_nested(self):
        with pytest.raises(ValidationError) as exc_info:
            TiptapDocSchema(
                type="doc",
                content=[
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "iframe"},
                        ],
                    },
                ],
            )
        assert "Unknown node type" in str(exc_info.value)

    def test_rejects_wrong_root_type(self):
        with pytest.raises(ValidationError):
            TiptapDocSchema(type="paragraph", content=[])

    def test_accepts_empty_content(self):
        doc = TiptapDocSchema(type="doc")
        assert doc.content == []

    def test_model_dump_preserves_structure(self):
        raw = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Hi"}]},
            ],
        }
        doc = TiptapDocSchema(**raw)
        dumped = doc.model_dump()
        assert dumped["type"] == "doc"
        assert dumped["content"][0]["type"] == "paragraph"


# ── API integration: content validation via endpoint ─────────────────────────


@pytest.mark.django_db
class TestTiptapSchemaEndpoint:
    def test_rejects_unknown_node_type_via_api(self):
        """U4: PATCH /cms/pages/{slug}/ rejects content with unknown node types."""
        user, token = _make_editor()
        resp = _patch(
            "home",
            user,
            token,
            {
                "content": {
                    "type": "doc",
                    "content": [{"type": "malicious_script"}],
                }
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_rejects_wrong_root_type_via_api(self):
        user, token = _make_editor()
        resp = _patch(
            "home",
            user,
            token,
            {
                "content": {
                    "type": "paragraph",
                    "content": [],
                }
            },
        )
        assert resp.status_code == 400

    def test_accepts_valid_doc_via_api(self):
        user, token = _make_editor()
        resp = _patch(
            "home",
            user,
            token,
            {
                "content": {
                    "type": "doc",
                    "content": [
                        {"type": "heading", "content": [{"type": "text", "text": "О компании"}]},
                    ],
                }
            },
        )
        assert resp.status_code == 200
        assert resp.json()["content"]["type"] == "doc"
