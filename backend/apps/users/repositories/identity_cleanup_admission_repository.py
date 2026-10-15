"""Unused-by-runtime foundation repository; never performs Redis I/O."""

import hashlib
import hmac
import secrets
from dataclasses import asdict, dataclass, field
from uuid import UUID, uuid4

from django.db.models import F
from django.utils import timezone

from apps.users.models import IdentityMergeRedisCleanupRecord
from apps.users.models_cleanup_protocol import IdentityCleanupAdmission
from apps.users.security.identity_cleanup_admission import (
    CleanupAdmissionError,
    CleanupTerminalReceipt,
    cleanup_catalog_phase,
)
from apps.users.security.identity_merge_legal_hold import require_no_legal_hold
from apps.users.security.identity_merge_locking import IdentityMergeLockCoordinator


@dataclass(frozen=True, slots=True)
class CleanupAdmissionOwner:
    admission_id: UUID
    owner_incarnation: UUID
    capability: str = field(repr=False)
    version: int = 1


class IdentityCleanupAdmissionRepository:
    @staticmethod
    def admit(
        *,
        cleanup_id: UUID,
        expected_cleanup_version: int,
        expected_operation_version: int,
        build_id: str,
        resource_epoch: str,
        using: str = "default",
    ) -> CleanupAdmissionOwner:
        with cleanup_catalog_phase(using=using):
            candidate = (
                IdentityMergeRedisCleanupRecord.objects.using(using).select_related("operation").get(pk=cleanup_id)
            )
            graph = IdentityMergeLockCoordinator().lock(user_pks=[candidate.operation.source_user_id], using=using)
            require_no_legal_hold(graph, using=using)
            cleanup = IdentityMergeRedisCleanupRecord.objects.using(using).select_for_update().get(pk=cleanup_id)
            operation = next(row for row in graph.operations if row.pk == cleanup.operation_id)
            if (
                cleanup.version != expected_cleanup_version
                or operation.version != expected_operation_version
                or cleanup.status != "pending"
            ):
                raise CleanupAdmissionError("CLEANUP_ADMISSION_STALE")
            actors = sorted(set(cleanup.actor_user_pks))
            if not actors or not set(actors).issubset({user.pk for user in graph.users}):
                raise CleanupAdmissionError("CLEANUP_ACTOR_SCOPE_INVALID")
            capability = secrets.token_hex(32)
            incarnation = uuid4()
            admission = IdentityCleanupAdmission.objects.using(using).create(
                cleanup=cleanup,
                operation=operation,
                expected_cleanup_version=expected_cleanup_version,
                expected_operation_version=expected_operation_version,
                identity_ids=list(graph.identity_ids),
                actor_pks=actors,
                tenant_ids=list(graph.tenant_ids),
                owner_incarnation=incarnation,
                owner_hash=hashlib.sha256(capability.encode()).hexdigest(),
                build_id=build_id,
                resource_epoch=resource_epoch,
            )
            result = CleanupAdmissionOwner(admission.pk, incarnation, capability)
        return result

    @staticmethod
    def record_terminal_receipt(
        *,
        owner: CleanupAdmissionOwner,
        expected_version: int,
        receipt: CleanupTerminalReceipt,
        using: str = "default",
    ) -> bool:
        """Store executor attestation only; never mark business cleanup complete."""
        with cleanup_catalog_phase(using=using):
            persisted = (
                IdentityCleanupAdmission.objects.using(using)
                .filter(
                    pk=owner.admission_id,
                    owner_incarnation=owner.owner_incarnation,
                    state="TERMINAL",
                )
                .first()
            )
            if persisted is not None:
                return (
                    persisted.version == expected_version + 1
                    and persisted.receipt == asdict(receipt)
                    and hmac.compare_digest(persisted.owner_hash, hashlib.sha256(owner.capability.encode()).hexdigest())
                )
            return (
                IdentityCleanupAdmission.objects.using(using)
                .filter(
                    pk=owner.admission_id,
                    owner_incarnation=owner.owner_incarnation,
                    version=expected_version,
                    state="OPEN",
                )
                .update(
                    state="TERMINAL",
                    version=F("version") + 1,
                    owner_proof=owner.capability,
                    receipt=asdict(receipt),
                    outcome=receipt.outcome,
                    updated_at=timezone.now(),
                )
                == 1
            )

    @staticmethod
    def record_unknown(*, owner: CleanupAdmissionOwner, expected_version: int, using: str = "default") -> bool:
        """An uncertain command outcome keeps the durable blocker indefinitely."""
        with cleanup_catalog_phase(using=using):
            return (
                IdentityCleanupAdmission.objects.using(using)
                .filter(
                    pk=owner.admission_id,
                    owner_incarnation=owner.owner_incarnation,
                    version=expected_version,
                    state="OPEN",
                )
                .update(
                    state="UNKNOWN",
                    version=F("version") + 1,
                    owner_proof=owner.capability,
                    outcome="UNKNOWN",
                    updated_at=timezone.now(),
                )
                == 1
            )
