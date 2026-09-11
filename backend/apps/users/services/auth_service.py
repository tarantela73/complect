import uuid
from datetime import UTC, datetime, timedelta
from typing import Optional

import jwt
from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.users.models import MfaCredential, User
from apps.users.security.identity_merge_legal_hold import IdentityMergeLegalHoldActive, require_no_legal_hold
from apps.users.security.identity_merge_locking import IdentityMergeLockCoordinator
from apps.users.services.deferred_merge_guard import DeferredMergeGuard

ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=7)
REFRESH_TOKEN_REDIS_PREFIX = "auth:refresh:"


def _pk_to_uuid4(pk: int) -> str:
    """Encode integer pk as deterministic UUID v4 (version=4, variant=RFC4122 bits set)."""
    return str(uuid.UUID(int=0x00000000000040008000000000000000 | (pk & 0xFFFFFFFFFFFF)))


def _get_user_permissions(user: User) -> list[str]:
    """Returns user permissions as ["resource.action", ...] codename format."""
    if not user.tenant_id:
        return []
    from apps.common.models.rbac import RoleBinding

    bindings = (
        RoleBinding.objects.filter(user_id=_pk_to_uuid4(user.pk), tenant_id=user.tenant_id, is_active=True)
        .select_related("role")
        .prefetch_related("role__permissions")
    )
    return [f"{perm.resource}.{perm.action}" for binding in bindings for perm in binding.role.permissions.all()]


def _make_access_token(user: User) -> str:
    now = datetime.now(UTC)
    authority = DeferredMergeGuard.resolve_legacy_actor(user)
    payload = {
        "user_id": _pk_to_uuid4(user.pk),
        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
        "username": user.username,
        "identity_id": str(authority.identity.identity_id) if authority.identity.identity_id else None,
        "security_version": authority.identity.security_version,
        "credential_epoch": authority.merge_operation.credential_epoch if authority.merge_operation else 0,
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
        "type": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def _make_refresh_token(user: User) -> tuple[str, str]:
    jti = str(uuid.uuid4())
    now = datetime.now(UTC)
    authority = DeferredMergeGuard.resolve_legacy_actor(user)
    payload = {
        "user_id": str(user.pk),
        "identity_id": str(authority.identity.identity_id) if authority.identity.identity_id else None,
        "security_version": authority.identity.security_version,
        "credential_epoch": authority.merge_operation.credential_epoch if authority.merge_operation else 0,
        "jti": jti,
        "iat": now,
        "exp": now + REFRESH_TOKEN_TTL,
        "type": "refresh",
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
    return token, jti


def _store_refresh_token(redis_client, user_id: int, jti: str) -> None:
    key = f"{REFRESH_TOKEN_REDIS_PREFIX}{user_id}:{jti}"
    redis_client.setex(key, int(REFRESH_TOKEN_TTL.total_seconds()), "1")


def _revoke_refresh_token(redis_client, user_id: int, jti: str) -> None:
    key = f"{REFRESH_TOKEN_REDIS_PREFIX}{user_id}:{jti}"
    redis_client.delete(key)


def _is_refresh_token_valid(redis_client, user_id: int, jti: str) -> bool:
    key = f"{REFRESH_TOKEN_REDIS_PREFIX}{user_id}:{jti}"
    return redis_client.exists(key) == 1


def login(
    redis_client,
    username: str,
    password: str,
    device_fingerprint: str = "",
) -> tuple[Optional[str], Optional[str], bool, list[str]]:
    with transaction.atomic(using="default"):
        return _login_locked(redis_client, username, password, device_fingerprint)


def _login_locked(
    redis_client,
    username: str,
    password: str,
    device_fingerprint: str = "",
) -> tuple[Optional[str], Optional[str], bool, list[str]]:
    """
    Returns (access_token, refresh_token, mfa_required, permissions).
    Raises PermissionDenied on invalid credentials.
    """
    try:
        actor = User.objects.using("default").get(username=username)
    except User.DoesNotExist:
        raise PermissionDenied("INVALID_CREDENTIALS") from None

    graph = IdentityMergeLockCoordinator().lock(user_pks=[actor.pk], using="default")
    try:
        require_no_legal_hold(graph, using="default")
    except IdentityMergeLegalHoldActive:
        raise PermissionDenied("INVALID_CREDENTIALS") from None
    authority = DeferredMergeGuard.resolve_legacy_actor(actor)
    user = authority.identity
    if not user.is_active:
        raise PermissionDenied("INVALID_CREDENTIALS")

    if not check_password(password, user.password):
        raise PermissionDenied("INVALID_CREDENTIALS") from None

    if device_fingerprint and user.device_fingerprint and user.device_fingerprint != device_fingerprint:
        raise PermissionDenied("DEVICE_FINGERPRINT_MISMATCH") from None

    has_confirmed_factor = user.mfa_enabled or MfaCredential.objects.filter(
        identity_id=user.identity_id,
        confirmed_at__isnull=False,
        revoked_at__isnull=True,
    ).exists()
    if has_confirmed_factor:
        partial_payload = {
            "user_id": str(authority.legacy_actor.pk),
            "identity_id": str(user.identity_id) if user.identity_id else None,
            "security_version": user.security_version,
            "credential_epoch": authority.merge_operation.credential_epoch if authority.merge_operation else 0,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "type": "mfa_pending",
        }
        partial_token = jwt.encode(partial_payload, settings.SECRET_KEY, algorithm="HS256")
        return partial_token, None, True, []

    token_actor = authority.legacy_actor
    access = _make_access_token(token_actor)
    refresh, jti = _make_refresh_token(token_actor)
    _store_refresh_token(redis_client, token_actor.pk, jti)
    permissions = _get_user_permissions(token_actor)
    return access, refresh, False, permissions


def refresh_access_token(redis_client, refresh_token: str) -> str:
    with transaction.atomic(using="default"):
        return _refresh_access_token_locked(redis_client, refresh_token)


def _refresh_access_token_locked(redis_client, refresh_token: str) -> str:
    """Returns new access token. Raises PermissionDenied on invalid/expired token."""
    try:
        payload = jwt.decode(refresh_token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise PermissionDenied("REFRESH_TOKEN_EXPIRED") from None
    except jwt.InvalidTokenError:
        raise PermissionDenied("REFRESH_TOKEN_INVALID") from None

    if payload.get("type") != "refresh":
        raise PermissionDenied("REFRESH_TOKEN_INVALID") from None

    user_id = payload["user_id"]
    jti = payload["jti"]

    try:
        actor = User.objects.using("default").get(pk=user_id)
    except User.DoesNotExist:
        raise PermissionDenied("USER_NOT_FOUND") from None

    graph = IdentityMergeLockCoordinator().lock(user_pks=[actor.pk], using="default")
    try:
        require_no_legal_hold(graph, using="default")
    except IdentityMergeLegalHoldActive:
        raise PermissionDenied("REFRESH_TOKEN_REVOKED") from None
    authority = DeferredMergeGuard.resolve_legacy_actor(actor)
    if not authority.identity.is_active:
        raise PermissionDenied("USER_NOT_FOUND")
    expected_identity_id = str(authority.identity.identity_id) if authority.identity.identity_id else None
    expected_epoch = authority.merge_operation.credential_epoch if authority.merge_operation else 0
    if (
        (authority.merge_operation is not None and payload.get("identity_id") is None)
        or payload.get("identity_id") not in {None, expected_identity_id}
        or payload.get("security_version") not in {None, authority.identity.security_version}
        or payload.get("credential_epoch", 0) != expected_epoch
    ):
        raise PermissionDenied("REFRESH_TOKEN_REVOKED")
    if not _is_refresh_token_valid(redis_client, user_id, jti):
        raise PermissionDenied("REFRESH_TOKEN_REVOKED") from None

    # Rotate refresh token (one-time use)
    _revoke_refresh_token(redis_client, user_id, jti)
    new_refresh, new_jti = _make_refresh_token(actor)
    _store_refresh_token(redis_client, actor.pk, new_jti)

    access = _make_access_token(actor)
    return access, new_refresh


def logout(redis_client, refresh_token: str) -> None:
    """Revokes refresh token. Silent on invalid token."""
    try:
        payload = jwt.decode(refresh_token, settings.SECRET_KEY, algorithms=["HS256"])
        if payload.get("type") == "refresh":
            actor = User.objects.using("default").get(pk=payload["user_id"])
            with transaction.atomic(using="default"):
                graph = IdentityMergeLockCoordinator().lock(user_pks=[actor.pk], using="default")
                require_no_legal_hold(graph, using="default")
                DeferredMergeGuard.resolve_legacy_actor(actor)
                _revoke_refresh_token(redis_client, payload["user_id"], payload["jti"])
    except (jwt.InvalidTokenError, User.DoesNotExist, PermissionDenied):
        pass
