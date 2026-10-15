import threading

import pytest
from django.db import close_old_connections, connections

from apps.users.services.identity_collision_resolution import (
    IdentityCollisionResolutionError,
    IdentityCollisionResolutionService,
)


@pytest.mark.django_db(
    transaction=True,
    databases=["default", "replica", "iam_concurrency_a", "iam_concurrency_b"],
)
def test_opposite_merge_approvals_serialize_without_postgresql_deadlock(
    collision_pair,
    signed_reconciliation_approval,
):
    first, second = collision_pair.first, collision_pair.second
    approval_ab = signed_reconciliation_approval.for_merge(
        source_pk=first.pk,
        canonical_pk=second.pk,
        reference="IAM-RACE-A-B",
    )
    approval_ba = signed_reconciliation_approval.for_merge(
        source_pk=second.pk,
        canonical_pk=first.pk,
        reference="IAM-RACE-B-A",
    )
    barrier = threading.Barrier(2)
    results = []
    result_lock = threading.Lock()

    def run(alias, source_pk, canonical_pk, approval):
        close_old_connections()
        try:
            with connections[alias].cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
            barrier.wait(timeout=5)
            operation = IdentityCollisionResolutionService(using=alias).approve_merge(
                source_pk=source_pk,
                canonical_pk=canonical_pk,
                approval=approval,
            )
            outcome = f"ok:{operation.id}"
        except IdentityCollisionResolutionError as exc:
            outcome = exc.code.value
        except Exception as exc:  # the assertion below exposes SQLSTATE/deadlock failures
            outcome = f"unexpected:{type(exc).__name__}:{exc}"
        finally:
            connections[alias].close()
        with result_lock:
            results.append(outcome)

    threads = [
        threading.Thread(target=run, args=("iam_concurrency_a", first.pk, second.pk, approval_ab)),
        threading.Thread(target=run, args=("iam_concurrency_b", second.pk, first.pk, approval_ba)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert all(not thread.is_alive() for thread in threads)
    assert sum(outcome.startswith("ok:") for outcome in results) == 1, results
    assert all("40P01" not in outcome and "DeadlockDetected" not in outcome for outcome in results)
    assert all(not outcome.startswith("unexpected:") for outcome in results)
