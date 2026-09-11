from dataclasses import dataclass

from django.core.exceptions import PermissionDenied

from apps.users.models import IdentityMergeOperation, User


@dataclass(frozen=True, slots=True)
class CredentialAuthority:
    legacy_actor: User
    identity: User
    merge_operation: IdentityMergeOperation | None

    @property
    def is_alias(self) -> bool:
        return self.merge_operation is not None


class DeferredMergeGuard:
    AUTHORITY_STATES = {
        IdentityMergeOperation.State.APPROVED_DEFERRED,
        IdentityMergeOperation.State.READY_TO_FINALIZE,
        IdentityMergeOperation.State.FINALIZED,
    }

    @classmethod
    def resolve_legacy_actor(cls, actor: User, *, using: str = "default") -> CredentialAuthority:
        current = actor
        seen = set()
        selected_operation = None
        while current.identity_id is not None:
            if current.identity_id in seen:
                raise PermissionDenied("MERGE_AUTHORITY_CYCLE")
            seen.add(current.identity_id)
            operations = list(
                IdentityMergeOperation.objects.using(using)
                .filter(source_user=current, state__in=cls.AUTHORITY_STATES)
                .order_by("id")[:2],
            )
            if not operations:
                break
            if len(operations) != 1:
                raise PermissionDenied("MERGE_AUTHORITY_AMBIGUOUS")
            operation = operations[0]
            canonical = list(
                User.objects.using(using).filter(identity_id=operation.canonical_identity_id).order_by("pk")[:2],
            )
            if len(canonical) != 1:
                raise PermissionDenied("MERGE_CANONICAL_IDENTITY_INVALID")
            selected_operation = selected_operation or operation
            current = canonical[0]
        return CredentialAuthority(
            legacy_actor=actor,
            identity=current,
            merge_operation=selected_operation,
        )

    @staticmethod
    def require_activation_allowed(identity_id, *, using: str = "default") -> None:
        blocked = IdentityMergeOperation.objects.using(using).filter(
            canonical_identity_id=identity_id,
            state__in=[
                IdentityMergeOperation.State.APPROVED_DEFERRED,
                IdentityMergeOperation.State.READY_TO_FINALIZE,
            ],
            convergence_mode=IdentityMergeOperation.ConvergenceMode.DORMANT_FAIL_CLOSED,
        ).exists()
        if blocked:
            raise PermissionDenied("MERGE_DORMANT_FINALIZATION_REQUIRED")
