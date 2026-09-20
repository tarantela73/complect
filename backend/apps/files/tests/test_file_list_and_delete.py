"""
Tests for:
  - FileRepository.soft_delete (legal hold guard, tenant isolation, CAS version bump)
  - get_files_page (cursor pagination, status filter, deleted exclusion)
"""

from __future__ import annotations

import hashlib
import uuid

import pytest

from apps.common.tenancy.context import set_tenant_context
from apps.files.models.file_record import FileRecord, FileStatus
from apps.files.queries.file_queries import get_files_page
from apps.files.repositories.file_repo import FileRepository
from apps.files.schemas.file_dto import UploadInitRequest
from apps.files.services.upload_init_service import UploadInitService
from apps.users.services.auth_service import _make_access_token

_SHA = hashlib.sha256(b"qa").hexdigest()


def _make_file(tenant_id: uuid.UUID, *, status: str = FileStatus.AVAILABLE) -> FileRecord:
    actor = uuid.uuid4()
    set_tenant_context(tenant_id)
    req = UploadInitRequest(original_name="qa.txt", content_type="text/plain", size_bytes=10)
    result = UploadInitService.initialize_upload(actor_id=actor, request=req)
    record = FileRepository.get_by_id(result.file_id)
    record.status = status
    record.checksum_sha256 = _SHA
    record.save()
    return record


# ---------------------------------------------------------------------------
# FileRepository.soft_delete
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSoftDelete:
    def setup_method(self):
        self.tenant = uuid.uuid4()
        set_tenant_context(self.tenant)

    def test_soft_delete_marks_deleted_and_bumps_version(self):
        f = _make_file(self.tenant)
        v0 = f.version
        ok = FileRepository.soft_delete(f.id)
        f.refresh_from_db()
        assert ok is True
        assert f.is_deleted is True
        assert f.version == v0 + 1

    def test_soft_delete_legal_hold_returns_false(self):
        f = _make_file(self.tenant)
        f.is_legal_hold = True
        f.save()
        ok = FileRepository.soft_delete(f.id)
        f.refresh_from_db()
        assert ok is False
        assert f.is_deleted is False

    def test_soft_delete_already_deleted_returns_false(self):
        f = _make_file(self.tenant)
        FileRepository.soft_delete(f.id)
        ok = FileRepository.soft_delete(f.id)
        assert ok is False

    def test_soft_delete_cross_tenant_returns_false(self):
        f = _make_file(self.tenant)
        other_tenant = uuid.uuid4()
        set_tenant_context(other_tenant)
        ok = FileRepository.soft_delete(f.id)
        assert ok is False
        f.refresh_from_db()
        assert f.is_deleted is False


# ---------------------------------------------------------------------------
# get_files_page — cursor pagination
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetFilesPage:
    def setup_method(self):
        self.tenant = uuid.uuid4()
        set_tenant_context(self.tenant)

    def _make_n(self, n: int) -> list[FileRecord]:
        return [_make_file(self.tenant) for _ in range(n)]

    def test_returns_all_when_under_limit(self):
        self._make_n(3)
        result = get_files_page(limit=10)
        assert result["count"] == 3
        assert result["next_cursor"] is None

    def test_cursor_pagination_next_page(self):
        files = self._make_n(5)
        page1 = get_files_page(limit=3)
        assert page1["count"] == 3
        assert page1["next_cursor"] is not None

        page2 = get_files_page(cursor=uuid.UUID(page1["next_cursor"]), limit=3)
        assert page2["count"] == 2
        assert page2["next_cursor"] is None

        ids_p1 = {r.id for r in page1["results"]}
        ids_p2 = {r.id for r in page2["results"]}
        assert ids_p1.isdisjoint(ids_p2)
        assert ids_p1 | ids_p2 == {f.id for f in files}

    def test_status_filter_excludes_other_statuses(self):
        _make_file(self.tenant, status=FileStatus.AVAILABLE)
        _make_file(self.tenant, status=FileStatus.SCANNING)
        result = get_files_page(status=FileStatus.AVAILABLE)
        assert result["count"] == 1
        assert result["results"][0].status == FileStatus.AVAILABLE

    def test_soft_deleted_excluded_from_list(self):
        f = _make_file(self.tenant)
        FileRepository.soft_delete(f.id)
        result = get_files_page()
        ids = {r.id for r in result["results"]}
        assert f.id not in ids

    def test_cross_tenant_files_excluded(self):
        _make_file(self.tenant)
        other = uuid.uuid4()
        _make_file(other)
        set_tenant_context(self.tenant)
        result = get_files_page()
        assert result["count"] == 1


# ---------------------------------------------------------------------------
# DELETE /api/v1/files/{id}/ endpoint
# ---------------------------------------------------------------------------

GW_TOKEN = "complect_internal_secure_mesh_token_2026"


@pytest.mark.django_db(transaction=True)
class TestDeleteFileEndpoint:
    @pytest.fixture(autouse=True)
    def setup(self):
        from apps.users.models import User

        self.tenant_id = uuid.uuid4()
        self.user = User.objects.create_user(
            username="test_delete_file",
            email="test_delete_file@example.com",
            password="TestPass123!",
            tenant_id=self.tenant_id,
        )
        self.access_token = _make_access_token(self.user)

    def _auth(self):
        return {
            "HTTP_X_GATEWAY_TOKEN": GW_TOKEN,
            "HTTP_AUTHORIZATION": f"Bearer {self.access_token}",
            "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
        }

    def test_delete_available_file_returns_204(self, client):
        from django.test import override_settings

        f = _make_file(self.tenant_id, status=FileStatus.AVAILABLE)
        with override_settings(RBAC_ENFORCEMENT_MODE="OFF"):
            resp = client.delete(f"/api/v1/files/{f.id}/", **self._auth())
        assert resp.status_code == 204
        f.refresh_from_db()
        assert f.is_deleted is True

    def test_delete_legal_hold_returns_403(self, client):
        from django.test import override_settings

        f = _make_file(self.tenant_id, status=FileStatus.AVAILABLE)
        f.is_legal_hold = True
        f.save(update_fields=["is_legal_hold"])
        with override_settings(RBAC_ENFORCEMENT_MODE="OFF"):
            resp = client.delete(f"/api/v1/files/{f.id}/", **self._auth())
        assert resp.status_code == 403

    def test_delete_unknown_file_returns_404(self, client):
        from django.test import override_settings

        with override_settings(RBAC_ENFORCEMENT_MODE="OFF"):
            resp = client.delete(f"/api/v1/files/{uuid.uuid4()}/", **self._auth())
        assert resp.status_code == 404
