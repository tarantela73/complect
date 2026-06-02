import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pyotp
import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied

from apps.users.models import User
from apps.users.services.auth_service import REFRESH_TOKEN_REDIS_PREFIX
from apps.users.services.mfa_service import (
    confirm_totp_setup,
    setup_totp,
    verify_mfa_and_issue_tokens,
)


def _make_user(mfa_enabled: bool = False, mfa_secret: str = "") -> User:
    return User.objects.create_user(
        username=f"user_{uuid.uuid4().hex[:8]}",
        password="Str0ng!Pass",
        tenant_id=uuid.uuid4(),
        mfa_enabled=mfa_enabled,
        mfa_secret=mfa_secret,
    )


@pytest.fixture()
def redis():
    return settings.REDIS_CLIENT


@pytest.mark.django_db(transaction=True)
class TestSetupTotp:
    def test_returns_secret_and_otpauth_url(self):
        user = _make_user()
        secret, otpauth_url = setup_totp(user)

        assert len(secret) >= 16
        assert "otpauth://totp/" in otpauth_url
        assert user.username in otpauth_url

    def test_saves_secret_to_db(self):
        user = _make_user()
        secret, _ = setup_totp(user)

        user.refresh_from_db()
        assert user.mfa_secret == secret
        assert user.mfa_enabled is False  # ещё не подтверждён

    def test_does_not_enable_mfa_until_confirmed(self):
        user = _make_user()
        setup_totp(user)

        user.refresh_from_db()
        assert user.mfa_enabled is False


@pytest.mark.django_db(transaction=True)
class TestConfirmTotpSetup:
    def test_valid_code_enables_mfa(self):
        user = _make_user()
        secret, _ = setup_totp(user)
        code = pyotp.TOTP(secret).now()

        confirm_totp_setup(user, code)

        user.refresh_from_db()
        assert user.mfa_enabled is True

    def test_invalid_code_raises(self):
        user = _make_user()
        setup_totp(user)

        with pytest.raises(PermissionDenied, match="MFA_CODE_INVALID"):
            confirm_totp_setup(user, "000000")

    def test_no_secret_raises(self):
        user = _make_user()  # mfa_secret пустой

        with pytest.raises(PermissionDenied, match="MFA_SECRET_NOT_INITIALIZED"):
            confirm_totp_setup(user, "123456")


@pytest.mark.django_db(transaction=True)
class TestVerifyMfaAndIssueTokens:
    def _make_partial_token(self, user: User, expire_in: timedelta = timedelta(minutes=5)) -> str:
        return jwt.encode(
            {
                "user_id": str(user.pk),
                "exp": datetime.now(UTC) + expire_in,
                "type": "mfa_pending",
            },
            settings.SECRET_KEY,
            algorithm="HS256",
        )

    def test_valid_code_issues_tokens(self, redis):
        secret = pyotp.random_base32()
        user = _make_user(mfa_enabled=True, mfa_secret=secret)
        partial = self._make_partial_token(user)
        code = pyotp.TOTP(secret).now()

        access, refresh = verify_mfa_and_issue_tokens(redis, partial, code)

        access_payload = jwt.decode(access, settings.SECRET_KEY, algorithms=["HS256"])
        assert access_payload["type"] == "access"
        assert access_payload["user_id"] == str(user.pk)

        refresh_payload = jwt.decode(refresh, settings.SECRET_KEY, algorithms=["HS256"])
        jti = refresh_payload["jti"]
        key = f"{REFRESH_TOKEN_REDIS_PREFIX}{user.pk}:{jti}"
        assert redis.exists(key) == 1

    def test_invalid_totp_code_raises(self, redis):
        secret = pyotp.random_base32()
        user = _make_user(mfa_enabled=True, mfa_secret=secret)
        partial = self._make_partial_token(user)

        with pytest.raises(PermissionDenied, match="MFA_CODE_INVALID"):
            verify_mfa_and_issue_tokens(redis, partial, "000000")

    def test_expired_partial_token_raises(self, redis):
        secret = pyotp.random_base32()
        user = _make_user(mfa_enabled=True, mfa_secret=secret)
        expired_partial = self._make_partial_token(user, expire_in=timedelta(seconds=-1))
        code = pyotp.TOTP(secret).now()

        with pytest.raises(PermissionDenied, match="MFA_SESSION_EXPIRED"):
            verify_mfa_and_issue_tokens(redis, expired_partial, code)

    def test_wrong_token_type_raises(self, redis):
        secret = pyotp.random_base32()
        user = _make_user(mfa_enabled=True, mfa_secret=secret)
        # access-токен вместо mfa_pending
        wrong = jwt.encode(
            {
                "user_id": str(user.pk),
                "exp": datetime.now(UTC) + timedelta(minutes=5),
                "type": "access",
            },
            settings.SECRET_KEY,
            algorithm="HS256",
        )
        code = pyotp.TOTP(secret).now()

        with pytest.raises(PermissionDenied, match="MFA_TOKEN_INVALID"):
            verify_mfa_and_issue_tokens(redis, wrong, code)

    def test_garbage_token_raises(self, redis):
        with pytest.raises(PermissionDenied, match="MFA_TOKEN_INVALID"):
            verify_mfa_and_issue_tokens(redis, "not.a.token", "123456")

    def test_mfa_not_enabled_user_raises(self, redis):
        user = _make_user(mfa_enabled=False)  # MFA не включён
        partial = self._make_partial_token(user)
        code = pyotp.TOTP(pyotp.random_base32()).now()

        with pytest.raises(PermissionDenied, match="USER_NOT_FOUND"):
            verify_mfa_and_issue_tokens(redis, partial, code)
