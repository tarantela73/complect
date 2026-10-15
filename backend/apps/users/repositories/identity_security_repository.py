from django.db.models import F, Q

from apps.users.models import User


class IdentitySecurityConcurrentUpdateError(RuntimeError):
    pass


class IdentitySecurityRepository:
    @staticmethod
    def initialize_legal_hold_default(*, identity: User, using: str = "default") -> bool:
        if identity.identity_legal_hold is not None:
            return False
        return (
            User.objects.using(using).filter(
                pk=identity.pk,
                identity_legal_hold__isnull=True,
            ).update(identity_legal_hold=False)
            == 1
        )

    @staticmethod
    def initialize_foundation(
        *,
        identity: User,
        updates: dict[str, object],
        using: str = "default",
    ) -> None:
        if not updates:
            return
        changed = User.objects.using(using).filter(
            pk=identity.pk,
            identity_id=identity.identity_id,
            normalized_email=identity.normalized_email,
            email_normalization_version=identity.email_normalization_version,
            security_version=identity.security_version,
            security_status=identity.security_status,
            identity_legal_hold=False,
        ).update(**updates)
        if changed != 1:
            raise IdentitySecurityConcurrentUpdateError("IDENTITY_FOUNDATION_CONTEXT_CHANGED")

    @staticmethod
    def stage_legacy_totp(*, identity: User, secret: str, using: str = "default") -> None:
        changed = User.objects.using(using).filter(
            Q(identity_legal_hold=False) | Q(identity_legal_hold__isnull=True),
            pk=identity.pk,
            security_version=identity.security_version,
            mfa_secret=identity.mfa_secret,
            mfa_enabled=identity.mfa_enabled,
        ).update(mfa_secret=secret, mfa_enabled=False)
        if changed != 1:
            raise IdentitySecurityConcurrentUpdateError("IDENTITY_SECURITY_CONTEXT_CHANGED")

    @staticmethod
    def confirm_legacy_totp(*, identity: User, using: str = "default") -> None:
        updates: dict[str, object] = {"mfa_enabled": True}
        if identity.security_version is not None:
            updates["security_version"] = F("security_version") + 1
        changed = User.objects.using(using).filter(
            Q(identity_legal_hold=False) | Q(identity_legal_hold__isnull=True),
            pk=identity.pk,
            security_version=identity.security_version,
            mfa_secret=identity.mfa_secret,
            mfa_enabled=False,
        ).update(**updates)
        if changed != 1:
            raise IdentitySecurityConcurrentUpdateError("IDENTITY_SECURITY_CONTEXT_CHANGED")
