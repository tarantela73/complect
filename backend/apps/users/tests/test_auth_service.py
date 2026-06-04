import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied

from apps.users.models import User
from apps.users.services.auth_service import (
    REFRESH_TOKEN_REDIS_PREFIX,
    login,
    logout,
    refresh_access_token,
)


def _make_user(
    username: str | None = None,
    password: str = "Str0ng!Pass",
    mfa_enabled: bool = False,
    device_fingerprint: str = "",
    is_active: bool = True,
) -> User:
    username = username or f"user_{uuid.uuid4().hex[:8]}"
    user = User.objects.create_user(
        username=username,
        password=password,
        tenant_id=uuid.uuid4(),
        mfa_enabled=mfa_enabled,
        device_fingerprint=device_fingerprint,
        is_active=is_active,
    )
    return user


@pytest.fixture()
def redis():
    return settings.REDIS_CLIENT


@pytest.mark.django_db(transaction=True)
class TestLogin:
    def test_invalid_password_raises(self, redis):
        user = _make_user()
        with pytest.raises(PermissionDenied, match="INVALID_CREDENTIALS"):
            login(redis, user.username, "wrong_password")

    def test_unknown_username_raises(self, redis):
        with pytest.raises(PermissionDenied, match="INVALID_CREDENTIALS"):
            login(redis, "nobody", "any")

    def test_inactive_user_raises(self, redis):
        user = _make_user(is_active=False)
        with pytest.raises(PermissionDenied, match="INVALID_CREDENTIALS"):
            login(redis, user.username, "Str0ng!Pass")

    def test_successful_login_returns_tokens(self, redis):
        user = _make_user()
        access, refresh, mfa_required, permissions = login(redis, user.username, "Str0ng!Pass")

        assert mfa_required is False
        assert access is not None
        assert refresh is not None

        access_payload = jwt.decode(access, settings.SECRET_KEY, algorithms=["HS256"])
        assert access_payload["type"] == "access"
        assert access_payload["user_id"] == str(user.pk)

        refresh_payload = jwt.decode(refresh, settings.SECRET_KEY, algorithms=["HS256"])
        assert refresh_payload["type"] == "refresh"
        jti = refresh_payload["jti"]
        key = f"{REFRESH_TOKEN_REDIS_PREFIX}{user.pk}:{jti}"
        assert redis.exists(key) == 1

    def test_mfa_enabled_returns_partial_token(self, redis):
        user = _make_user(mfa_enabled=True)
        partial, refresh, mfa_required, permissions = login(redis, user.username, "Str0ng!Pass")

        assert mfa_required is True
        assert refresh is None

        payload = jwt.decode(partial, settings.SECRET_KEY, algorithms=["HS256"])
        assert payload["type"] == "mfa_pending"
        assert payload["user_id"] == str(user.pk)

    def test_device_fingerprint_mismatch_raises(self, redis):
        user = _make_user(device_fingerprint="fp_registered")
        with pytest.raises(PermissionDenied, match="DEVICE_FINGERPRINT_MISMATCH"):
            login(redis, user.username, "Str0ng!Pass", device_fingerprint="fp_stranger")

    def test_matching_device_fingerprint_succeeds(self, redis):
        user = _make_user(device_fingerprint="fp_ok")
        access, _, mfa_required, _ = login(redis, user.username, "Str0ng!Pass", device_fingerprint="fp_ok")
        assert mfa_required is False
        assert access is not None


@pytest.mark.django_db(transaction=True)
class TestRefreshAccessToken:
    def test_valid_refresh_returns_new_access_and_rotates(self, redis):
        user = _make_user()
        _, refresh, _, _ = login(redis, user.username, "Str0ng!Pass")

        old_payload = jwt.decode(refresh, settings.SECRET_KEY, algorithms=["HS256"])
        old_jti = old_payload["jti"]

        new_access, new_refresh = refresh_access_token(redis, refresh)

        assert new_access is not None
        assert new_refresh != refresh

        # Старый jti удалён из Redis
        old_key = f"{REFRESH_TOKEN_REDIS_PREFIX}{user.pk}:{old_jti}"
        assert redis.exists(old_key) == 0

        # Новый jti сохранён
        new_jti = jwt.decode(new_refresh, settings.SECRET_KEY, algorithms=["HS256"])["jti"]
        new_key = f"{REFRESH_TOKEN_REDIS_PREFIX}{user.pk}:{new_jti}"
        assert redis.exists(new_key) == 1

    def test_revoked_token_raises(self, redis):
        user = _make_user()
        _, refresh, _, _ = login(redis, user.username, "Str0ng!Pass")

        # Первый refresh отзывает старый jti
        refresh_access_token(redis, refresh)

        # Повторное использование того же токена → отозван
        with pytest.raises(PermissionDenied, match="REFRESH_TOKEN_REVOKED"):
            refresh_access_token(redis, refresh)

    def test_expired_token_raises(self, redis):
        user = _make_user()
        expired = jwt.encode(
            {
                "user_id": str(user.pk),
                "jti": str(uuid.uuid4()),
                "iat": datetime.now(UTC) - timedelta(days=8),
                "exp": datetime.now(UTC) - timedelta(days=1),
                "type": "refresh",
            },
            settings.SECRET_KEY,
            algorithm="HS256",
        )
        with pytest.raises(PermissionDenied, match="REFRESH_TOKEN_EXPIRED"):
            refresh_access_token(redis, expired)

    def test_wrong_token_type_raises(self, redis):
        user = _make_user()
        access, _, _, _ = login(redis, user.username, "Str0ng!Pass")
        with pytest.raises(PermissionDenied, match="REFRESH_TOKEN_INVALID"):
            refresh_access_token(redis, access)

    def test_garbage_token_raises(self, redis):
        with pytest.raises(PermissionDenied, match="REFRESH_TOKEN_INVALID"):
            refresh_access_token(redis, "not.a.token")


@pytest.mark.django_db(transaction=True)
class TestLogout:
    def test_logout_deletes_refresh_token(self, redis):
        user = _make_user()
        _, refresh, _, _ = login(redis, user.username, "Str0ng!Pass")

        payload = jwt.decode(refresh, settings.SECRET_KEY, algorithms=["HS256"])
        key = f"{REFRESH_TOKEN_REDIS_PREFIX}{user.pk}:{payload['jti']}"
        assert redis.exists(key) == 1

        logout(redis, refresh)
        assert redis.exists(key) == 0

    def test_logout_invalid_token_is_silent(self, redis):
        # Не должен бросать исключений
        logout(redis, "garbage.token.value")

    def test_logout_already_logged_out_is_silent(self, redis):
        user = _make_user()
        _, refresh, _, _ = login(redis, user.username, "Str0ng!Pass")
        logout(redis, refresh)
        logout(redis, refresh)  # повторный — молчит
