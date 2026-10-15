import pytest

from apps.users.models import IdentityMergeOperation
from apps.users.repositories.merge_convergence_repository import (
    MergeConvergenceCasConflictError,
    MergeConvergenceRepository,
)


@pytest.mark.django_db(transaction=True)
def test_stale_dormant_transition_is_typed_conflict_and_changes_nothing(identity_merge_operation_factory):
    operation = identity_merge_operation_factory()
    operation.version += 1

    with pytest.raises(MergeConvergenceCasConflictError, match="MERGE_CONTEXT_STALE"):
        MergeConvergenceRepository.transition_to_deferred(
            operation=operation,
            mode=IdentityMergeOperation.ConvergenceMode.DORMANT_FAIL_CLOSED,
        )

    operation.refresh_from_db(using="default")
    assert operation.state == IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE
    assert operation.version == 1
