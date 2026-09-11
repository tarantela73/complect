import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.common.tenancy.context import tenant_rls_scope
from apps.users.models import (
    BackupCode,
    IdentityMergeHoldBlock,
    IdentityMergeOperation,
    IdentityMergeRedisCleanupRecord,
    IdentitySession,
    MfaCredential,
    PreAuthChallenge,
    RecoveryOperation,
    RecoveryToken,
    SessionReconciliationRecord,
    TenantAuthorizationState,
    TenantGrantSwitchOperation,
    TenantMembership,
    TenantSessionGrant,
)
from apps.users.models_identity import MergeActionKind, MergeHoldSubjectType


@dataclass(frozen=True, slots=True, order=True)
class LegalHoldSubject:
    subject_type: str
    identity_id: uuid.UUID
    reference: str = "LEGAL_HOLD"


@dataclass(frozen=True, slots=True)
class LegalHoldSnapshot:
    subjects: tuple[LegalHoldSubject, ...]
    operation_versions: tuple[tuple[uuid.UUID, int], ...]
    digest: str
    lock_plan_digest: str

    @property
    def active(self) -> bool:
        return bool(self.subjects)

    def operation_version(self, operation_id: uuid.UUID) -> int:
        return dict(self.operation_versions)[operation_id]


class IdentityMergeLegalHoldActive(PermissionDenied):
    code = "LEGAL_HOLD_ACTIVE"

    def __init__(self, snapshot: LegalHoldSnapshot):
        self.snapshot = snapshot
        super().__init__(self.code)


def collect_legal_hold_snapshot(graph: Any, *, using: str = "default") -> LegalHoldSnapshot:
    subjects: set[LegalHoldSubject] = set()
    canonical_ids = {operation.canonical_identity_id for operation in graph.operations}

    def add_identity(identity_id: uuid.UUID | None, subject_type: str | None = None) -> None:
        if identity_id is None:
            return
        resolved_type = subject_type or (
            MergeHoldSubjectType.CANONICAL_IDENTITY
            if identity_id in canonical_ids
            else MergeHoldSubjectType.SOURCE_IDENTITY
        )
        subjects.add(LegalHoldSubject(resolved_type, identity_id))

    for user in graph.users:
        if user.identity_legal_hold:
            add_identity(user.identity_id)
    for operation in graph.operations:
        if operation.is_legal_hold:
            subjects.add(
                LegalHoldSubject(
                    MergeHoldSubjectType.OPERATION,
                    operation.source_identity_id,
                    operation.legal_hold_reference or "LEGAL_HOLD",
                ),
            )
    for model in (MfaCredential, BackupCode, RecoveryToken, PreAuthChallenge, IdentitySession):
        for identity_id in model.objects.using(using).filter(
            identity_id__in=graph.identity_ids,
            is_legal_hold=True,
        ).values_list("identity_id", flat=True):
            add_identity(identity_id)
    for identity_id in IdentityMergeRedisCleanupRecord.objects.using(using).filter(
        operation_id__in=[operation.id for operation in graph.operations],
        is_legal_hold=True,
    ).values_list("operation__source_identity_id", flat=True):
        add_identity(identity_id)
    for identity_id in RecoveryOperation.objects.using(using).filter(
        token__identity_id__in=graph.identity_ids,
        is_legal_hold=True,
    ).values_list("token__identity_id", flat=True):
        add_identity(identity_id)
    for identity_id in TenantGrantSwitchOperation.objects.using(using).filter(
        session__identity_id__in=graph.identity_ids,
        is_legal_hold=True,
    ).values_list("session__identity_id", flat=True):
        add_identity(identity_id, MergeHoldSubjectType.SESSION)
    for identity_id in SessionReconciliationRecord.objects.using(using).filter(
        session__identity_id__in=graph.identity_ids,
        is_legal_hold=True,
    ).values_list("session__identity_id", flat=True):
        add_identity(identity_id, MergeHoldSubjectType.SESSION)

    actor_pks = [user.pk for user in graph.users]
    fallback_identity = graph.identity_ids[0] if graph.identity_ids else None
    for tenant_id in graph.tenant_ids:
        with tenant_rls_scope(tenant_id, using=using):
            if TenantAuthorizationState.objects.using(using).filter(
                tenant_id=tenant_id,
                is_legal_hold=True,
            ).exists():
                add_identity(fallback_identity)
            held_memberships = TenantMembership.objects.using(using).filter(
                tenant_id=tenant_id,
                legacy_actor_user_id__in=actor_pks,
                is_deleted=False,
                is_legal_hold=True,
            )
            for identity_id in held_memberships.values_list("identity_id", flat=True):
                add_identity(identity_id, MergeHoldSubjectType.MEMBERSHIP)
            for identity_id in TenantSessionGrant.objects.using(using).filter(
                tenant_id=tenant_id,
                membership__legacy_actor_user_id__in=actor_pks,
                is_legal_hold=True,
            ).values_list("membership__identity_id", flat=True):
                add_identity(identity_id, MergeHoldSubjectType.GRANT)

    ordered = tuple(sorted(subjects))
    encoded = json.dumps(
        [(subject.subject_type, str(subject.identity_id), subject.reference) for subject in ordered],
        separators=(",", ":"),
    ).encode()
    lock_plan = json.dumps(
        {
            "users": [user.pk for user in graph.users],
            "operations": [(str(operation.id), operation.version) for operation in graph.operations],
            "tenants": [str(tenant_id) for tenant_id in graph.tenant_ids],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return LegalHoldSnapshot(
        subjects=ordered,
        operation_versions=tuple((operation.id, operation.version) for operation in graph.operations),
        digest=hashlib.sha256(encoded).hexdigest(),
        lock_plan_digest=hashlib.sha256(lock_plan).hexdigest(),
    )


def require_no_legal_hold(graph: Any, *, using: str = "default") -> LegalHoldSnapshot:
    snapshot = collect_legal_hold_snapshot(graph, using=using)
    if snapshot.active:
        raise IdentityMergeLegalHoldActive(snapshot)
    return snapshot


def record_hold_evidence(
    *,
    operation_id: uuid.UUID,
    snapshot: LegalHoldSnapshot,
    action_digest: str,
    action_kind: str = MergeActionKind.OWNER_CONVERGENCE,
    request_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
    using: str = "default",
) -> IdentityMergeHoldBlock:
    """Persist only append-only evidence after the authority transaction rolled back."""
    if not snapshot.active:
        raise ValueError("LEGAL_HOLD_SNAPSHOT_EMPTY")
    subject = snapshot.subjects[0]
    with transaction.atomic(using=using):
        operation = IdentityMergeOperation.objects.using(using).select_for_update().get(pk=operation_id)
        block, _created = IdentityMergeHoldBlock.objects.using(using).get_or_create(
            operation=operation,
            subject_type=subject.subject_type,
            subject_identity_id=subject.identity_id,
            hold_digest=snapshot.digest,
            defaults={
                "hold_reference": subject.reference,
                "action_digest": action_digest,
                "action_kind": action_kind,
                "lock_plan_digest": snapshot.lock_plan_digest,
                "hold_set_digest": snapshot.digest,
                "operation_version_snapshot": snapshot.operation_version(operation.id),
                "request_id": request_id or uuid.uuid4(),
                "correlation_id": correlation_id or uuid.uuid4(),
                "observed_at": timezone.now(),
            },
        )
        return block
