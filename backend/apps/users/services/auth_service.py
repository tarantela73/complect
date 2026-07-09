import uuid
from datetime import UTC, datetime, timedelta
from typing import Optional

import jwt
from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core.exceptions import PermissionDenied

from apps.users.models import User

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
    payload = {
        "user_id": _pk_to_uuid4(user.pk),
        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
        "username": user.username,
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
        "type": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def _make_refresh_token(user: User) -> tuple[str, str]:
    jti = str(uuid.uuid4())
    now = datetime.now(UTC)
    payload = {
        "user_id": str(user.pk),
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
    """
    Returns (access_token, refresh_token, mfa_required, permissions).
    Raises PermissionDenied on invalid credentials.
    """
    try:
        user = User.objects.get(username=username, is_active=True)
    except User.DoesNotExist:
        raise PermissionDenied("INVALID_CREDENTIALS") from None

    if not check_password(password, user.password):
        raise PermissionDenied("INVALID_CREDENTIALS") from None

    if device_fingerprint and user.device_fingerprint and user.device_fingerprint != device_fingerprint:
        raise PermissionDenied("DEVICE_FINGERPRINT_MISMATCH") from None

    if user.mfa_enabled:
        partial_payload = {
            "user_id": str(user.pk),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "type": "mfa_pending",
        }
        partial_token = jwt.encode(partial_payload, settings.SECRET_KEY, algorithm="HS256")
        return partial_token, None, True, []

    access = _make_access_token(user)
    refresh, jti = _make_refresh_token(user)
    _store_refresh_token(redis_client, user.pk, jti)
    permissions = _get_user_permissions(user)
    return access, refresh, False, permissions


def refresh_access_token(redis_client, refresh_token: str) -> str:
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

    if not _is_refresh_token_valid(redis_client, user_id, jti):
        raise PermissionDenied("REFRESH_TOKEN_REVOKED") from None

    try:
        user = User.objects.get(pk=user_id, is_active=True)
    except User.DoesNotExist:
        raise PermissionDenied("USER_NOT_FOUND") from None

    # Rotate refresh token (one-time use)
    _revoke_refresh_token(redis_client, user_id, jti)
    new_refresh, new_jti = _make_refresh_token(user)
    _store_refresh_token(redis_client, user.pk, new_jti)

    access = _make_access_token(user)
    return access, new_refresh


def logout(redis_client, refresh_token: str) -> None:
    """Revokes refresh token. Silent on invalid token."""
    try:
        payload = jwt.decode(refresh_token, settings.SECRET_KEY, algorithms=["HS256"])
        if payload.get("type") == "refresh":
            _revoke_refresh_token(redis_client, payload["user_id"], payload["jti"])
    except jwt.InvalidTokenError:
        pass
