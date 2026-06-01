
import jwt
import pyotp
from django.conf import settings
from django.core.exceptions import PermissionDenied

from apps.users.models import User
from apps.users.services.auth_service import (
    _make_access_token,
    _make_refresh_token,
    _store_refresh_token,
)


def setup_totp(user: User) -> tuple[str, str]:
    """Generates a new TOTP secret and saves it (not yet enabled). Returns (secret, otpauth_url)."""
    secret = pyotp.random_base32()
    User.objects.filter(pk=user.pk).update(mfa_secret=secret, mfa_enabled=False)
    totp = pyotp.TOTP(secret)
    otpauth_url = totp.provisioning_uri(name=user.username, issuer_name="ERP Комплект")
    return secret, otpauth_url


def confirm_totp_setup(user: User, code: str) -> None:
    """Verifies code against the pending secret and enables MFA."""
    user.refresh_from_db()
    if not user.mfa_secret:
        raise PermissionDenied("MFA_SECRET_NOT_INITIALIZED") from None
    totp = pyotp.TOTP(user.mfa_secret)
    if not totp.verify(code, valid_window=1):
        raise PermissionDenied("MFA_CODE_INVALID") from None
    User.objects.filter(pk=user.pk).update(mfa_enabled=True)


def verify_mfa_and_issue_tokens(redis_client, partial_token: str, code: str) -> tuple[str, str]:
    """
    Validates partial MFA token + TOTP code, returns (access_token, refresh_token).
    Raises PermissionDenied on any failure.
    """
    try:
        payload = jwt.decode(partial_token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise PermissionDenied("MFA_SESSION_EXPIRED") from None
    except jwt.InvalidTokenError:
        raise PermissionDenied("MFA_TOKEN_INVALID") from None

    if payload.get("type") != "mfa_pending":
        raise PermissionDenied("MFA_TOKEN_INVALID") from None

    try:
        user = User.objects.get(pk=payload["user_id"], is_active=True, mfa_enabled=True)
    except User.DoesNotExist:
        raise PermissionDenied("USER_NOT_FOUND") from None

    totp = pyotp.TOTP(user.mfa_secret)
    if not totp.verify(code, valid_window=1):
        raise PermissionDenied("MFA_CODE_INVALID") from None

    access = _make_access_token(user)
    refresh, jti = _make_refresh_token(user)
    _store_refresh_token(redis_client, user.pk, jti)
    return access, refresh
