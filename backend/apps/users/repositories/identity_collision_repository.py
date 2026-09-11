from dataclasses import dataclass
from uuid import UUID

from apps.common.tenancy.context import tenant_rls_scope
from apps.users.infrastructure.iam_tenant_inventory import DurableIamTenantInventoryProvider
from apps.users.models import IdentityMergeOperation, TenantMembership, User


@dataclass(frozen=True, slots=True)
class IdentitySubject:
    user_pk: int
    identity_id: UUID | None


@dataclass(frozen=True, slots=True)
class IdentityPiiCandidate:
    user_pk: int
    identity_id: UUID | None
    email: str
    username: str
    tenant_id: UUID | None


class IdentityCollisionRepository:
    @staticmethod
    def enumerate_subjects(*, user_pks: list[int] | None = None, using: str = "default") -> list[IdentitySubject]:
        queryset = User.objects.using(using).order_by("pk")
        if user_pks is not None:
            queryset = queryset.filter(pk__in=user_pks)
        return [
            IdentitySubject(user_pk=pk, identity_id=identity_id)
            for pk, identity_id in queryset.values_list("pk", "identity_id")
        ]

    @staticmethod
    def correct_email_cas(
        *,
        user: User,
        expected_security_version: int,
        updates: dict[str, object],
        using: str = "default",
    ) -> bool:
        return (
            User.objects.using(using).filter(
                pk=user.pk,
                security_version=expected_security_version,
                identity_legal_hold=False,
            ).update(**updates)
            == 1
        )

    @staticmethod
    def load_pii_candidates(user_pks: list[int], *, using: str = "default") -> list[IdentityPiiCandidate]:
        rows = (
            User.objects.using(using)
            .filter(pk__in=user_pks)
            .order_by("pk")
            .values_list("pk", "identity_id", "email", "username", "tenant_id")
        )
        return [IdentityPiiCandidate(*row) for row in rows]

    @staticmethod
    def lock_users(user_pks: list[int], *, using: str = "default") -> list[User]:
        return list(
            User.objects.using(using).select_for_update().filter(pk__in=user_pks).order_by("pk"),
        )

    @staticmethod
    def resolved_deferred_source_user_pks(*, using: str = "default") -> set[int]:
        """Return only source rows backed by the complete relational proof."""
        resolved: set[int] = set()
        operations = (
            IdentityMergeOperation.objects.using(using)
            .filter(
                state__in=[
                    IdentityMergeOperation.State.APPROVED_DEFERRED,
                    IdentityMergeOperation.State.READY_TO_FINALIZE,
                    IdentityMergeOperation.State.FINALIZED,
                ],
            )
            .select_related("source_user")
            .order_by("id")
        )
        tenant_ids = list(DurableIamTenantInventoryProvider(using=using).iter_tenant_ids())
        for operation in operations:
            source = operation.source_user
            canonical_count = User.objects.using(using).filter(identity_id=operation.canonical_identity_id).count()
            if (
                canonical_count != 1
                or source.identity_id != operation.source_identity_id
                or source.normalized_email != operation.synthetic_normalized_email
            ):
                continue
            actor_memberships = []
            for tenant_id in tenant_ids:
                with tenant_rls_scope(tenant_id, using=using):
                    actor_memberships.extend(
                        TenantMembership.objects.using(using).filter(
                            tenant_id=tenant_id,
                            legacy_actor_user_id=source.pk,
                            is_deleted=False,
                        ).values_list("tenant_id", "identity_id"),
                    )
            legacy_tenant_is_present = any(
                tenant_id == source.tenant_id for tenant_id, _identity in actor_memberships
            )
            if source.tenant_id and not legacy_tenant_is_present:
                continue
            if all(identity_id == operation.canonical_identity_id for _tenant_id, identity_id in actor_memberships):
                resolved.add(source.pk)
        return resolved
