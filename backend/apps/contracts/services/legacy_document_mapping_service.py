from __future__ import annotations

import re
import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest

from apps.common.api.auth import verified_request_context
from apps.common.context.core import get_correlation_id
from apps.common.contracts.legacy_document_contracts import (
    FileProof,
    LegacyFileProofProtocol,
    LegacyLink,
    LegacySourceProof,
    LegacySourceVerificationProtocol,
)
from apps.common.security.policy_engine import PolicyEngine
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.tenancy.context import tenant_rls_scope
from apps.contracts.events.legacy_document_mapping import LegacyDocumentMappingChanged
from apps.contracts.repositories.legacy_document_link_repository import LegacyDocumentLinkRepository


class LegacyDocumentMappingService:
    def __init__(
        self, source_verifier: LegacySourceVerificationProtocol, file_proof_provider: LegacyFileProofProtocol
    ) -> None:
        self._source_verifier = source_verifier
        self._file_proof_provider = file_proof_provider

    def record_verified(self, source: LegacySourceProof, target_file_id: uuid.UUID, request: HttpRequest) -> LegacyLink:
        context = verified_request_context(request)
        if context is None or context.tenant_id != source.tenant_id:
            raise PermissionDenied("verified tenant context is required")
        if getattr(settings, "RBAC_ENFORCEMENT_MODE", "OFF") == "OFF":
            raise PermissionDenied("mapping writes require enforced RBAC")
        if not PolicyEngine.check(
            actor_id=context.actor_id, tenant_id=context.tenant_id, action="write", resource="legacy_document_mapping"
        ):
            raise PermissionDenied("mapping permission denied")
            raise ValidationError("legacy source proof was not verified")
        if source.source_system != "COMMON_DOCUMENT":
            raise ValidationError("unsupported legacy source system")
        if any(
            not value
            for value in (
                source.revision,
                source.namespace,
                source.object_key,
                source.checksum_sha256,
                source.snapshot_sha256,
                source.payload_sha256,
            )
        ):
            raise ValidationError("legacy source proof is incomplete")
        if source.size_bytes < 0 or any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (source.checksum_sha256, source.snapshot_sha256, source.payload_sha256)
        ):
            raise ValidationError("legacy source proof hashes are invalid")
        target: FileProof = self._file_proof_provider.get_proof(target_file_id, context.tenant_id)
        if not self._source_verifier.verify(source, target):
            raise ValidationError("legacy source proof not verified")
        if (
            target.file_id != target_file_id
            or target.tenant_id != context.tenant_id
            or target.object_key != source.object_key
            or target.size_bytes != source.size_bytes
            or target.checksum_sha256 != source.checksum_sha256
            or target.status not in {"UPLOADED", "SCANNING", "QUARANTINED", "AVAILABLE"}
            or target.is_legal_hold
        ):
            raise ValidationError("target file proof does not match source proof")
        with tenant_rls_scope(context.tenant_id):
            link, changed = LegacyDocumentLinkRepository.record_verified(
                source=source,
                target_file_id=target_file_id,
                actor_id=context.actor_id,
                target_version=target.version,
            )
            if changed:
                TransactionalOutboxService.push_to_outbox(
                    LegacyDocumentMappingChanged.create(
                        correlation_id=get_correlation_id(),
                        mapping_id=link.id,
                        legacy_document_id=link.legacy_document_id,
                        target_file_id=link.target_file_id,
                        proposed_target_file_id=target_file_id,
                        actor_id=context.actor_id,
                        mapping_state=link.mapping_state,
                        mapping_version=link.version,
                        proposed_snapshot_sha256=source.snapshot_sha256,
                    )
                )
        if link.target_file_id is None or link.mapping_state != "RESOLVED":
            raise ValidationError("legacy mapping conflicted or unresolved")
        return LegacyLink(
            source=source, target_file_id=link.target_file_id, version=link.version, target_version=link.target_version
        )
