import uuid

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.users.contracts.auth_context_impl import UsersAuthContextValidator
from apps.users.services.auth_service import _pk_to_uuid4


@pytest.mark.django_db(transaction=True)
def test_legacy_validator_qualifies_actor_pk_by_expected_tenant_in_the_lookup(identity):
    wrong_tenant = uuid.uuid4()
    claims = {
        "user_id": _pk_to_uuid4(identity.pk),
        "tenant_id": str(wrong_tenant),
        "type": "access",
    }

    with CaptureQueriesContext(connection) as captured:
        with pytest.raises(PermissionDenied, match="INVALID_AUTH_CONTEXT"):
            UsersAuthContextValidator.validate(claims)

    user_queries = [query["sql"] for query in captured.captured_queries if "users_users" in query["sql"]]
    assert user_queries
    assert any('"users_users"."tenant_id" =' in sql for sql in user_queries)
