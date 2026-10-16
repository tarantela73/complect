from __future__ import annotations

import uuid
from collections.abc import Callable

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction

from apps.common.contracts.legacy_document_contracts import LegacyLink, LegacySourceProof
from apps.common.repository.sre_engine import EnterpriseSreRepository
from apps.common.tenancy.context import get_current_tenant_id, tenant_rls_scope
from apps.contracts.models.legacy_document_link import (
    LegacyDocumentLink,
    LegacyDocumentMappingState,
)


class LegacyDocumentLinkRepository:
    """CAS-backed persistence for the Contracts-owned compatibility record."""

    @staticmethod
    def resolve(legacy_document_id: uuid.UUID, tenant_id: uuid.UUID):
        if get_current_tenant_id() != tenant_id:
            return None
        link = (
            LegacyDocumentLink.objects.filter(
                tenant_id=tenant_id,
                legacy_document_id=legacy_document_id,
                is_deleted=False,
                is_legal_hold=False,
            )
            .order_by("-version")
            .first()
        )
        if link is None or link.mapping_state != LegacyDocumentMappingState.RESOLVED or link.target_file_id is None:
            return None
        from apps.common.contracts.legacy_document_contracts import (
            LegacyLink,
            LegacySourceProof,
        )

        return LegacyLink(
            source=LegacySourceProof(
                source_system=link.source_system,
                legacy_document_id=link.legacy_document_id,
                tenant_id=link.tenant_id,
                revision=link.legacy_revision,
                namespace=link.storage_namespace,
                object_key=link.object_key,
                size_bytes=link.size_bytes,
                checksum_sha256=link.checksum_sha256,
                snapshot_sha256=link.source_snapshot_sha256,
                payload_sha256=link.payload_sha256,
            ),
            target_file_id=link.target_file_id,
            version=link.version,
            target_version=link.target_version,
        )

    @staticmethod
    def finalize_resolved(link: LegacyLink, operation: Callable[[], None]) -> None:
        # Lock order: contracts mapping, then Files aggregate. No external I/O.
        with tenant_rls_scope(link.source.tenant_id):
            LegacyDocumentLink.objects.select_for_update().get(
                legacy_document_id=link.source.legacy_document_id,
                tenant_id=link.source.tenant_id,
                source_system="COMMON_DOCUMENT",
                is_deleted=False,
                is_legal_hold=False,
                version=link.version,
            )
            if LegacyDocumentLinkRepository.resolve(link.source.legacy_document_id, link.source.tenant_id) != link:
                raise PermissionDenied("LEGACY_FINALIZATION_MAPPING_CHANGED")
            operation()

    @staticmethod
    def record_verified(
        *, source: LegacySourceProof, target_file_id: uuid.UUID, actor_id: uuid.UUID, target_version: int = 1
    ) -> tuple[LegacyDocumentLink, bool]:
        """Insert an immutable-proof mapping, or mark a divergent replay conflict."""
        tenant_id = source.tenant_id
        if get_current_tenant_id() != tenant_id:
            raise ValueError("tenant RLS context does not match source proof")

        values = {
            "tenant_id": tenant_id,
            "source_system": source.source_system,
            "actor_id": actor_id,
            "legacy_document_id": source.legacy_document_id,
            "target_file_id": target_file_id,
            "target_version": target_version,
            "legacy_revision": source.revision,
            "source_snapshot_sha256": source.snapshot_sha256,
            "payload_sha256": source.payload_sha256,
            "checksum_sha256": source.checksum_sha256,
            "storage_namespace": source.namespace,
            "object_key": source.object_key,
            "size_bytes": source.size_bytes,
            "mapping_state": LegacyDocumentMappingState.RESOLVED,
        }
        try:
            with transaction.atomic():
                return LegacyDocumentLink.objects.create(**values), True
        except IntegrityError:
            existing = LegacyDocumentLink.objects.get(
                tenant_id=tenant_id,
                source_system=source.source_system,
                legacy_document_id=source.legacy_document_id,
                is_deleted=False,
            )
            proof_matches = all(
                getattr(existing, field) == value
                for field, value in (
                    ("target_file_id", target_file_id),
                    ("legacy_revision", source.revision),
                    ("source_snapshot_sha256", source.snapshot_sha256),
                    ("payload_sha256", source.payload_sha256),
                    ("checksum_sha256", source.checksum_sha256),
                    ("storage_namespace", source.namespace),
                    ("object_key", source.object_key),
                    ("size_bytes", source.size_bytes),
                )
            )
            if proof_matches:
                return existing, False
            changed = existing.mapping_state != LegacyDocumentMappingState.CONFLICT
            if changed:
                EnterpriseSreRepository.atomic_cas_update(existing, mapping_state=LegacyDocumentMappingState.CONFLICT)
                existing.refresh_from_db()
            return existing, changed
