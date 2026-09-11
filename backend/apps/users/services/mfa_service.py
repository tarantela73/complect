import jwt
import pyotp
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.users.models import MfaCredential, User
from apps.users.repositories.identity_security_repository import (
    IdentitySecurityConcurrentUpdateError,
    IdentitySecurityRepository,
)
from apps.users.security.identity_merge_legal_hold import require_no_legal_hold
from apps.users.security.identity_merge_locking import IdentityMergeLockCoordinator
from apps.users.services.auth_service import (
    _make_access_token,
    _make_refresh_token,
    _store_refresh_token,
)
from apps.users.services.deferred_merge_guard import DeferredMergeGuard
from apps.users.services.merge_convergence_service import CanonicalTotpVerifier


def setup_totp(user: User) -> tuple[str, str]:
    """Generates a new TOTP secret and saves it (not yet enabled). Returns (secret, otpauth_url)."""
    with transaction.atomic(using="default"):
        graph = IdentityMergeLockCoordinator().lock(user_pks=[user.pk], using="default")
        require_no_legal_hold(graph, using="default")
        authority = DeferredMergeGuard.resolve_legacy_actor(user)
        DeferredMergeGuard.require_activation_allowed(authority.identity.identity_id)
        user = next(row for row in graph.users if row.pk == authority.identity.pk)
        secret = pyotp.random_base32()
        try:
            IdentitySecurityRepository.stage_legacy_totp(identity=user, secret=secret)
        except IdentitySecurityConcurrentUpdateError:
            raise PermissionDenied("IDENTITY_SECURITY_CONTEXT_CHANGED") from None
    totp = pyotp.TOTP(secret)
    otpauth_url = totp.provisioning_uri(name=user.username, issuer_name="ERP Комплект")
    return secret, otpauth_url


def confirm_totp_setup(user: User, code: str) -> None:
    """Verifies code against the pending secret and enables MFA."""
    with transaction.atomic(using="default"):
        graph = IdentityMergeLockCoordinator().lock(user_pks=[user.pk], using="default")
        require_no_legal_hold(graph, using="default")
        authority = DeferredMergeGuard.resolve_legacy_actor(user)
        DeferredMergeGuard.require_activation_allowed(authority.identity.identity_id)
        user = next(row for row in graph.users if row.pk == authority.identity.pk)
        if not user.mfa_secret:
            raise PermissionDenied("MFA_SECRET_NOT_INITIALIZED") from None
        totp = pyotp.TOTP(user.mfa_secret)
        if not totp.verify(code, valid_window=1):
            raise PermissionDenied("MFA_CODE_INVALID") from None
        try:
            IdentitySecurityRepository.confirm_legacy_totp(identity=user)
        except IdentitySecurityConcurrentUpdateError:
            raise PermissionDenied("IDENTITY_SECURITY_CONTEXT_CHANGED") from None


def verify_mfa_and_issue_tokens(redis_client, partial_token: str, code: str) -> tuple[str, str]:
    with transaction.atomic(using="default"):
        return _verify_mfa_and_issue_tokens_locked(redis_client, partial_token, code)


def _verify_mfa_and_issue_tokens_locked(redis_client, partial_token: str, code: str) -> tuple[str, str]:
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
        actor = User.objects.using("default").get(pk=payload["user_id"])
    except User.DoesNotExist:
        raise PermissionDenied("USER_NOT_FOUND") from None

    graph = IdentityMergeLockCoordinator().lock(user_pks=[actor.pk], using="default")
    require_no_legal_hold(graph, using="default")
    authority = DeferredMergeGuard.resolve_legacy_actor(actor)
    user = authority.identity
    has_confirmed_factor = user.mfa_enabled or MfaCredential.objects.filter(
        identity_id=user.identity_id,
        confirmed_at__isnull=False,
        revoked_at__isnull=True,
    ).exists()
    if not user.is_active or not has_confirmed_factor:
        raise PermissionDenied("USER_NOT_FOUND")
    expected_identity_id = str(user.identity_id) if user.identity_id else None
    expected_epoch = authority.merge_operation.credential_epoch if authority.merge_operation else 0
    if (
        (authority.merge_operation is not None and payload.get("identity_id") is None)
        or payload.get("identity_id") not in {None, expected_identity_id}
        or payload.get("security_version") not in {None, user.security_version}
        or payload.get("credential_epoch", 0) != expected_epoch
    ):
        raise PermissionDenied("MFA_TOKEN_INVALID")

    if not CanonicalTotpVerifier().verify(user, code):
        raise PermissionDenied("MFA_CODE_INVALID") from None

    access = _make_access_token(actor)
    refresh, jti = _make_refresh_token(actor)
    _store_refresh_token(redis_client, actor.pk, jti)
    return access, refresh
