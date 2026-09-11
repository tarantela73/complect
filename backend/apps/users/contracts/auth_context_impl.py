import uuid
from typing import Any

from django.core.exceptions import PermissionDenied

from apps.common.contracts.auth_context import ValidatedAuthContext
from apps.users.models import User
from apps.users.services.deferred_merge_guard import DeferredMergeGuard


class UsersAuthContextValidator:
    @staticmethod
    def validate(claims: dict[str, Any]) -> ValidatedAuthContext:
        try:
            actor_id = uuid.UUID(claims["user_id"])
            tenant_id = uuid.UUID(claims["tenant_id"])
        except (KeyError, TypeError, ValueError):
            raise PermissionDenied("INVALID_AUTH_CONTEXT") from None
        actor_pk = actor_id.int & 0xFFFFFFFFFFFF
        try:
            actor = User.objects.using("default").get(pk=actor_pk, tenant_id=tenant_id)
        except User.DoesNotExist:
            raise PermissionDenied("INVALID_AUTH_CONTEXT") from None
        authority = DeferredMergeGuard.resolve_legacy_actor(actor)
        if not authority.identity.is_active:
            raise PermissionDenied("INVALID_AUTH_CONTEXT")
        expected_identity_id = str(authority.identity.identity_id) if authority.identity.identity_id else None
        expected_epoch = authority.merge_operation.credential_epoch if authority.merge_operation else 0
        if (
            (authority.merge_operation is not None and claims.get("identity_id") is None)
            or claims.get("identity_id") not in {None, expected_identity_id}
            or claims.get("security_version") not in {None, authority.identity.security_version}
            or claims.get("credential_epoch", 0) != expected_epoch
        ):
            raise PermissionDenied("INVALID_AUTH_CONTEXT")
        return ValidatedAuthContext(actor_id=actor_id, tenant_id=tenant_id)


users_auth_context_validator = UsersAuthContextValidator()
