import pytest
from django.db import transaction

from apps.users.security.identity_merge_locking import IdentityMergeLockCoordinator


@pytest.mark.django_db(transaction=True)
def test_lock_coordinator_materializes_users_in_pk_order(collision_pair):
    with transaction.atomic():
        graph = IdentityMergeLockCoordinator().lock(
            user_pks=[collision_pair.second.pk, collision_pair.first.pk],
        )
        assert [user.pk for user in graph.users] == sorted(
            [collision_pair.first.pk, collision_pair.second.pk],
        )
