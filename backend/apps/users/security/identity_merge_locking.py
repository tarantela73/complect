import hashlib
from dataclasses import dataclass
from uuid import UUID

from django.db import connections
from django.db.models import Q

from apps.common.models.rbac import RoleBinding
from apps.common.tenancy.context import tenant_rls_scope
from apps.users.infrastructure.iam_tenant_inventory import (
    DurableIamTenantInventoryProvider,
    LegacyIamTenantDiscovery,
)
from apps.users.models import (
    BackupCode,
    IamTenantRegistryEntry,
    IdentityMergeHoldBlock,
    IdentityMergeOperation,
    IdentityMergeRedisCleanupRecord,
    IdentityMergeRequeue,
    IdentityMergeRequeueResult,
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
    User,
)


def _signed_advisory_key(namespace: bytes, value: bytes) -> int:
    raw = hashlib.sha256(namespace + b":" + value).digest()[:8]
    return int.from_bytes(raw, "big", signed=True)


def acquire_identity_merge_advisory_locks(
    *,
    user_pks: set[int] | list[int] | tuple[int, ...],
    identity_ids: set[UUID] | list[UUID] | tuple[UUID, ...],
    using: str = "default",
) -> None:
    """Acquire the graph's transaction locks in the one public order."""
    with connections[using].cursor() as cursor:
        for pk in sorted(set(user_pks)):
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                [_signed_advisory_key(b"iam-user", str(pk).encode())],
            )
        for identity_id in sorted(set(identity_ids), key=lambda value: value.bytes):
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                [_signed_advisory_key(b"iam-identity", identity_id.bytes)],
            )


@dataclass(frozen=True, slots=True)
class LockedIdentityMergeGraph:
    users: tuple[User, ...]
    operations: tuple[IdentityMergeOperation, ...]
    identity_ids: tuple[UUID, ...]
    tenant_ids: tuple[UUID, ...]


class IdentityMergeLockCoordinator:
    """One deterministic lock order for every merge-graph writer."""

    @staticmethod
    def _discover_identity_ids(seed_users: list[User], *, using: str) -> set[UUID]:
        identity_ids = {user.identity_id for user in seed_users if user.identity_id is not None}
        while identity_ids:
            rows = list(
                IdentityMergeOperation.objects.using(using)
                .exclude(state__in=["cancelled", "expired"])
                .filter(Q(source_identity_id__in=identity_ids) | Q(canonical_identity_id__in=identity_ids))
                .values_list("source_identity_id", "canonical_identity_id"),
            )
            expanded = identity_ids | {value for row in rows for value in row}
            if expanded == identity_ids:
                break
            identity_ids = expanded
        return identity_ids

    @staticmethod
    def _actor_uuid(pk: int) -> UUID:
        return UUID(int=0x00000000000040008000000000000000 | (pk & 0xFFFFFFFFFFFF))

    @classmethod
    def _discover_tenant_ids(
        cls,
        *,
        users: tuple[User, ...],
        identity_ids: set[UUID],
        candidate_tenant_ids: set[UUID],
        using: str,
    ) -> tuple[UUID, ...]:
        actor_pks = [user.pk for user in users]
        actor_ids = [cls._actor_uuid(pk) for pk in actor_pks]
        related = {user.tenant_id for user in users if user.tenant_id is not None}
        for tenant_id in sorted(candidate_tenant_ids, key=lambda value: value.bytes):
            with tenant_rls_scope(tenant_id, using=using):
                has_membership = TenantMembership.objects.using(using).filter(
                    tenant_id=tenant_id,
                ).filter(
                    Q(identity_id__in=identity_ids) | Q(legacy_actor_user_id__in=actor_pks),
                ).exists()
                has_role_binding = RoleBinding.objects.using(using).filter(
                    tenant_id=tenant_id,
                    user_id__in=actor_ids,
                ).exists()
            if has_membership or has_role_binding:
                related.add(tenant_id)
        return tuple(sorted(related, key=lambda value: value.bytes))

    @staticmethod
    def _lock_related_rows(
        *,
        users: tuple[User, ...],
        operations: tuple[IdentityMergeOperation, ...],
        identity_ids: set[UUID],
        tenant_ids: tuple[UUID, ...],
        using: str,
    ) -> None:
        operation_ids = [operation.id for operation in operations]

        list(
            IdentityMergeHoldBlock.objects.using(using)
            .select_for_update()
            .filter(operation_id__in=operation_ids)
            .order_by("id")
            .values_list("id", flat=True),
        )
        requeue_ids = list(
            IdentityMergeRequeue.objects.using(using)
            .select_for_update()
            .filter(operation_id__in=operation_ids)
            .order_by("id")
            .values_list("id", flat=True),
        )
        list(
            IdentityMergeRequeueResult.objects.using(using)
            .select_for_update()
            .filter(requeue_id__in=requeue_ids)
            .order_by("id")
            .values_list("id", flat=True),
        )

        list(
            IamTenantRegistryEntry.objects.using(using)
            .select_for_update()
            .filter(tenant_id__in=tenant_ids)
            .order_by("tenant_id")
            .values_list("tenant_id", flat=True),
        )
        for model in (MfaCredential, BackupCode):
            list(
                model.objects.using(using)
                .select_for_update()
                .filter(identity_id__in=identity_ids)
                .order_by("id")
                .values_list("id", flat=True),
            )

        recovery_operation_ids = list(
            RecoveryToken.objects.using(using)
            .filter(identity_id__in=identity_ids)
            .order_by("operation_id")
            .values_list("operation_id", flat=True),
        )
        list(
            RecoveryOperation.objects.using(using)
            .select_for_update()
            .filter(id__in=recovery_operation_ids)
            .order_by("id")
            .values_list("id", flat=True),
        )
        list(
            RecoveryToken.objects.using(using)
            .select_for_update()
            .filter(identity_id__in=identity_ids)
            .order_by("id")
            .values_list("id", flat=True),
        )
        list(
            PreAuthChallenge.objects.using(using)
            .select_for_update()
            .filter(Q(identity_id__in=identity_ids) | Q(merge_operation_id__in=operation_ids))
            .order_by("id")
            .values_list("id", flat=True),
        )
        session_ids = list(
            IdentitySession.objects.using(using)
            .select_for_update()
            .filter(identity_id__in=identity_ids)
            .order_by("id")
            .values_list("id", flat=True),
        )
        list(
            TenantGrantSwitchOperation.objects.using(using)
            .select_for_update()
            .filter(session_id__in=session_ids)
            .order_by("id")
            .values_list("id", flat=True),
        )
        list(
            SessionReconciliationRecord.objects.using(using)
            .select_for_update()
            .filter(session_id__in=session_ids)
            .order_by("id")
            .values_list("id", flat=True),
        )
        list(
            IdentityMergeRedisCleanupRecord.objects.using(using)
            .select_for_update()
            .filter(operation_id__in=operation_ids)
            .order_by("id")
            .values_list("id", flat=True),
        )

        actor_pks = [user.pk for user in users]
        actor_ids = [IdentityMergeLockCoordinator._actor_uuid(pk) for pk in actor_pks]
        for tenant_id in tenant_ids:
            with tenant_rls_scope(tenant_id, using=using):
                list(
                    TenantAuthorizationState.objects.using(using)
                    .select_for_update()
                    .filter(tenant_id=tenant_id)
                    .order_by("id")
                    .values_list("id", flat=True),
                )
                membership_ids = list(
                    TenantMembership.objects.using(using)
                    .select_for_update()
                    .filter(tenant_id=tenant_id)
                    .filter(Q(identity_id__in=identity_ids) | Q(legacy_actor_user_id__in=actor_pks))
                    .order_by("id")
                    .values_list("id", flat=True),
                )
                list(
                    RoleBinding.objects.using(using)
                    .select_for_update()
                    .filter(tenant_id=tenant_id, user_id__in=actor_ids)
                    .order_by("id")
                    .values_list("id", flat=True),
                )
                list(
                    TenantSessionGrant.objects.using(using)
                    .select_for_update()
                    .filter(tenant_id=tenant_id, membership_id__in=membership_ids)
                    .order_by("id")
                    .values_list("id", flat=True),
                )

    def lock(self, *, user_pks: list[int], using: str = "default") -> LockedIdentityMergeGraph:
        bootstrap_users = list(User.objects.using(using).filter(pk__in=user_pks).order_by("pk"))
        identity_ids = self._discover_identity_ids(bootstrap_users, using=using)
        discovered_user_pks = set(
            User.objects.using(using)
            .filter(Q(pk__in=user_pks) | Q(identity_id__in=identity_ids))
            .values_list("pk", flat=True),
        )
        acquire_identity_merge_advisory_locks(
            user_pks=discovered_user_pks,
            identity_ids=identity_ids,
            using=using,
        )
        users = tuple(
            User.objects.using(using)
            .select_for_update()
            .filter(Q(pk__in=user_pks) | Q(identity_id__in=identity_ids))
            .order_by("pk"),
        )
        locked_identity_ids = self._discover_identity_ids(list(users), using=using)
        if locked_identity_ids != identity_ids:
            from apps.users.services.identity_collision_resolution import (
                IdentityCollisionResolutionError,
                IdentityCollisionResolutionErrorCode,
            )

            raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.MERGE_GRAPH_CHANGED_RETRY)
        operations = tuple(
            IdentityMergeOperation.objects.using(using)
            .select_for_update()
            .filter(Q(source_identity_id__in=identity_ids) | Q(canonical_identity_id__in=identity_ids))
            .order_by("id"),
        ) if identity_ids else ()
        candidate_tenant_ids = set(DurableIamTenantInventoryProvider(using=using).iter_tenant_ids())
        candidate_tenant_ids.update(LegacyIamTenantDiscovery(using=using).iter_tenant_ids())
        candidate_tenant_ids.update(user.tenant_id for user in users if user.tenant_id is not None)
        sorted_tenant_ids = self._discover_tenant_ids(
            users=users,
            identity_ids=identity_ids,
            candidate_tenant_ids=candidate_tenant_ids,
            using=using,
        )
        self._lock_related_rows(
            users=users,
            operations=operations,
            identity_ids=identity_ids,
            tenant_ids=sorted_tenant_ids,
            using=using,
        )
        return LockedIdentityMergeGraph(
            users=users,
            operations=operations,
            identity_ids=tuple(sorted(identity_ids, key=lambda value: value.bytes)),
            tenant_ids=sorted_tenant_ids,
        )


def identity_merge_graph_has_legal_hold(
    graph: LockedIdentityMergeGraph,
    *,
    using: str = "default",
) -> bool:
    from apps.users.security.identity_merge_legal_hold import collect_legal_hold_snapshot

    return collect_legal_hold_snapshot(graph, using=using).active
