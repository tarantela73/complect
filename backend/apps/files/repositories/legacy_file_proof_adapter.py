"""Files-owned proof and guarded command transitions for legacy compatibility."""

from __future__ import annotations

import uuid

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.common.contracts.legacy_document_contracts import FileProof
from apps.common.events.files import FileAvailableEvent, FileQuarantinedEvent
from apps.common.repository.sre_engine import EnterpriseSreRepository
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.tenancy.context import tenant_rls_scope
from apps.files.models.file_record import FileRecord, FileStatus
from apps.files.repositories.file_repo import FileRepository


class LegacyFileProofAdapter:
    @staticmethod
    def confirm_available(proof: FileProof) -> None:
        with tenant_rls_scope(proof.tenant_id):
            record = (
                FileRecord.objects.using("default")
                .select_for_update()
                .get(
                    id=proof.file_id,
                    tenant_id=proof.tenant_id,
                    is_deleted=False,
                    version=proof.version,
                    status=FileStatus.AVAILABLE,
                )
            )
            if (
                record.object_key != proof.object_key
                or record.checksum_sha256 != proof.checksum_sha256
                or record.size_bytes != proof.size_bytes
            ):
                raise PermissionDenied("LEGACY_COMPLETED_PROOF_CHANGED")

    def get_proof(self, file_id: uuid.UUID, tenant_id: uuid.UUID) -> FileProof:
        with tenant_rls_scope(tenant_id):
            record = FileRepository.get_by_id(file_id)
            return FileProof(
                file_id=record.id,
                tenant_id=record.tenant_id,
                object_key=record.object_key,
                size_bytes=record.size_bytes,
                checksum_sha256=record.checksum_sha256,
                storage_provider=record.storage_provider,
                status=record.status,
                version=record.version,
                is_legal_hold=record.is_legal_hold,
            )

    @staticmethod
    def transition(
        proof: FileProof,
        *,
        status: str,
        correlation_id: uuid.UUID,
        reason: str = "",
    ) -> FileProof:
        with tenant_rls_scope(proof.tenant_id), transaction.atomic():
            record = (
                FileRecord.objects.using("default")
                .select_for_update()
                .get(
                    id=proof.file_id,
                    tenant_id=proof.tenant_id,
                    is_deleted=False,
                )
            )
            if record.version != proof.version or record.is_legal_hold:
                raise PermissionDenied("LEGACY_FILE_VERSION_OR_HOLD_CHANGED")
            if record.status == FileStatus.QUARANTINED and record.quarantine_reason == "virus_detected":
                raise PermissionDenied("LEGACY_INFECTED_FILE_BLOCKED")
            if status == FileStatus.SCANNING:
                if record.status not in {FileStatus.UPLOADED, FileStatus.SCANNING, FileStatus.QUARANTINED}:
                    raise PermissionDenied("LEGACY_FILE_NOT_SCANNABLE")
            elif record.status != FileStatus.SCANNING:
                raise PermissionDenied("LEGACY_FILE_NOT_SCANNING")
            EnterpriseSreRepository.atomic_cas_update(
                record,
                status=status,
                quarantine_reason=reason,
                scanned_at=timezone.now() if status in {FileStatus.AVAILABLE, FileStatus.QUARANTINED} else None,
            )
            if status == FileStatus.AVAILABLE:
                TransactionalOutboxService.push_to_outbox(
                    FileAvailableEvent.create(
                        correlation_id=correlation_id,
                        file_id=record.id,
                        tenant_id=record.tenant_id,
                        object_key=record.object_key,
                        checksum_sha256=record.checksum_sha256,
                    )
                )
            elif status == FileStatus.QUARANTINED:
                TransactionalOutboxService.push_to_outbox(
                    FileQuarantinedEvent.create(
                        correlation_id=correlation_id,
                        file_id=record.id,
                        tenant_id=record.tenant_id,
                        object_key=record.object_key,
                        quarantine_reason=reason,
                        uploaded_by=record.uploaded_by,
                    )
                )
        return LegacyFileProofAdapter().get_proof(proof.file_id, proof.tenant_id)
