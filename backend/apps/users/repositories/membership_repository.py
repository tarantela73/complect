from uuid import UUID

from django.db import transaction

from apps.common.repository.sre_engine import EnterpriseSreRepository
from apps.common.tenancy.context import tenant_rls_scope
from apps.users.models import TenantAuthorizationState, TenantMembership
from apps.users.repositories.iam_tenant_registry_repository import IamTenantRegistryRepository
from apps.users.security.identity_merge_locking import acquire_identity_merge_advisory_locks


class MembershipRepository:
    @staticmethod
    def ensure_authorization_state(
        tenant_id: UUID,
        *,
        using: str = "default",
    ) -> TenantAuthorizationState:
        with transaction.atomic(using=using):
            IamTenantRegistryRepository.ensure_registered(
                tenant_id,
                source_code="membership_foundation",
                using=using,
            )
            with tenant_rls_scope(tenant_id, using=using):
                state, _created = TenantAuthorizationState.objects.using(using).get_or_create(
                    tenant_id=tenant_id,
                    defaults={"policy_version": 1},
                )
                return state

    @staticmethod
    def ensure_foundation(
        *,
        tenant_id: UUID,
        identity_id: UUID,
        legacy_actor_user_id: int,
        display_name: str,
        status: str = TenantMembership.Status.ACTIVE,
        using: str = "default",
    ) -> TenantMembership:
        if legacy_actor_user_id is None:
            raise ValueError("legacy_actor_user_id is required")
        with transaction.atomic(using=using):
            acquire_identity_merge_advisory_locks(
                user_pks=[legacy_actor_user_id],
                identity_ids=[identity_id],
                using=using,
            )
            IamTenantRegistryRepository.ensure_registered(
                tenant_id,
                source_code="membership_foundation",
                using=using,
            )
            with tenant_rls_scope(tenant_id, using=using):
                TenantAuthorizationState.objects.using(using).get_or_create(
                    tenant_id=tenant_id,
                    defaults={"policy_version": 1},
                )
                membership, _created = TenantMembership.objects.using(using).get_or_create(
                    tenant_id=tenant_id,
                    identity_id=identity_id,
                    is_deleted=False,
                    defaults={
                        "legacy_actor_user_id": legacy_actor_user_id,
                        "display_name": display_name,
                        "status": status,
                    },
                )
                if membership.legacy_actor_user_id is None:
                    TenantMembership.objects.using(using).filter(
                        pk=membership.pk,
                        legacy_actor_user_id__isnull=True,
                    ).update(legacy_actor_user_id=legacy_actor_user_id)
                    membership.refresh_from_db(using=using)
                elif membership.legacy_actor_user_id != legacy_actor_user_id:
                    raise ValueError("membership actor is immutable")
                return membership

    @staticmethod
    def move_actor_memberships_to_identity(
        *,
        tenant_id: UUID,
        legacy_actor_user_ids: list[int],
        canonical_identity_id: UUID,
        using: str = "default",
    ) -> int:
        with tenant_rls_scope(tenant_id, using=using):
            memberships = list(
                TenantMembership.objects.using(using)
                .select_for_update()
                .filter(
                    tenant_id=tenant_id,
                    legacy_actor_user_id__in=legacy_actor_user_ids,
                    is_deleted=False,
                )
                .order_by("id"),
            )
            changed = 0
            for membership in memberships:
                if membership.identity_id == canonical_identity_id:
                    continue
                EnterpriseSreRepository.atomic_cas_update(
                    membership,
                    identity_id=canonical_identity_id,
                )
                changed += 1
            return changed
