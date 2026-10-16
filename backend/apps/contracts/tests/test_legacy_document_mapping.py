from __future__ import annotations

import uuid
from dataclasses import replace

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory

from apps.common.tenancy.context import tenant_rls_scope
from apps.contracts.models.legacy_document_link import LegacyDocumentLink
from apps.contracts.repositories.legacy_document_link_repository import LegacyDocumentLinkRepository
from apps.contracts.services.legacy_document_mapping_service import LegacyDocumentMappingService
from apps.files.repositories.file_repo import FileRepository
from apps.files.repositories.legacy_file_proof_adapter import LegacyFileProofAdapter

pytest_plugins = ["apps.common.tests.legacy_compat_fixtures"]

pytestmark = pytest.mark.django_db(transaction=True)


def test_real_auth_export_and_file_proof_record_distinct_ids_idempotently(legacy_authenticated_request, legacy_export):
    request, auth = legacy_authenticated_request
    source, file_id, verifier = legacy_export(auth.tenant_id)
    service = LegacyDocumentMappingService(verifier, LegacyFileProofAdapter())
    with tenant_rls_scope(auth.tenant_id):
        first = service.record_verified(source, file_id, request)
        assert source.legacy_document_id != file_id
        assert service.record_verified(source, file_id, request) == first
        assert FileRepository.mark_scanning(file_id)
        assert service.record_verified(source, file_id, request) == first
    with tenant_rls_scope(uuid.uuid4()):
        assert LegacyDocumentLinkRepository.resolve(source.legacy_document_id, auth.tenant_id) is None
        assert not LegacyDocumentLink.objects.filter(legacy_document_id=source.legacy_document_id).exists()


def test_missing_verified_context_never_writes(legacy_export):
    source, file_id, verifier = legacy_export(uuid.uuid4())
    with pytest.raises(PermissionDenied):
        LegacyDocumentMappingService(verifier, LegacyFileProofAdapter()).record_verified(
            source, file_id, RequestFactory().post("/")
        )


def test_off_mode_never_authorizes_mapping(legacy_authenticated_request, legacy_export, settings):
    request, auth = legacy_authenticated_request
    source, file_id, verifier = legacy_export(auth.tenant_id)
    settings.RBAC_ENFORCEMENT_MODE = "OFF"
    with pytest.raises(PermissionDenied):
        LegacyDocumentMappingService(verifier, LegacyFileProofAdapter()).record_verified(source, file_id, request)


@pytest.mark.parametrize(
    "field,value", [("object_key", "unproven-key"), ("checksum_sha256", "0" * 64), ("size_bytes", 1)]
)
def test_source_target_mismatch_never_creates_mapping(legacy_authenticated_request, legacy_export, field, value):
    request, auth = legacy_authenticated_request
    source, file_id, verifier = legacy_export(auth.tenant_id)
    with pytest.raises(ValidationError):
        LegacyDocumentMappingService(verifier, LegacyFileProofAdapter()).record_verified(
            replace(source, **{field: value}), file_id, request
        )
    with tenant_rls_scope(auth.tenant_id):
        assert not LegacyDocumentLink.objects.filter(legacy_document_id=source.legacy_document_id).exists()


def test_database_rejects_incomplete_resolved_mapping():
    tenant_id = uuid.uuid4()
    with tenant_rls_scope(tenant_id), pytest.raises(IntegrityError), transaction.atomic():
        LegacyDocumentLink.objects.create(
            tenant_id=tenant_id,
            source_system="COMMON_DOCUMENT",
            legacy_document_id=uuid.uuid4(),
            mapping_state="RESOLVED",
            size_bytes=1,
        )
