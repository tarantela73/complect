import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from django.core.exceptions import ValidationError

from apps.users.models import User
from apps.users.repositories.identity_security_repository import IdentitySecurityRepository
from apps.users.services.email_normalization import NormalizedEmail, normalize_email


@dataclass(frozen=True, slots=True)
class IdentityFoundation:
    identity_id: uuid.UUID
    normalized_email: NormalizedEmail
    security_version: int
    security_status: str
    identity_legal_hold: bool
    changed: bool


@dataclass(frozen=True, slots=True)
class CollisionIdentity:
    user_pk: int
    identity_id: uuid.UUID | None
    email: str
    username: str
    tenant_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class EmailCollisionGroup:
    normalized_email: str
    identities: tuple[CollisionIdentity, ...]


@dataclass(frozen=True, slots=True)
class InvalidIdentityCandidate:
    user_pk: int
    identity_id: uuid.UUID | None
    error_code: str


def _candidate_value(candidate: Any) -> str:
    return (getattr(candidate, "email", "") or getattr(candidate, "username", "")).strip()


def classify_collision_candidates(
    candidates: Iterable[Any],
) -> tuple[list[EmailCollisionGroup], list[InvalidIdentityCandidate]]:
    grouped: dict[str, list[CollisionIdentity]] = {}
    invalid: list[InvalidIdentityCandidate] = []
    for candidate in candidates:
        try:
            normalized = normalize_email(_candidate_value(candidate)).value
        except ValidationError as exc:
            code = str(exc.messages[0]) if exc.messages else "INVALID_EMAIL"
            invalid.append(
                InvalidIdentityCandidate(
                    user_pk=candidate.user_pk,
                    identity_id=candidate.identity_id,
                    error_code=code,
                ),
            )
            continue
        grouped.setdefault(normalized, []).append(
            CollisionIdentity(
                user_pk=candidate.user_pk,
                identity_id=candidate.identity_id,
                email=candidate.email,
                username=candidate.username,
                tenant_id=candidate.tenant_id,
            ),
        )
    groups = [
        EmailCollisionGroup(normalized_email=value, identities=tuple(rows))
        for value, rows in sorted(grouped.items())
        if len(rows) > 1
    ]
    return groups, invalid


def build_collision_groups(candidates: Iterable[Any]) -> list[EmailCollisionGroup]:
    groups, _invalid = classify_collision_candidates(candidates)
    return groups


def serialize_groups(groups: Iterable[EmailCollisionGroup]) -> list[dict[str, object]]:
    return [
        {
            "normalized_email": group.normalized_email,
            "identities": [
                {
                    "user_pk": row.user_pk,
                    "identity_id": str(row.identity_id) if row.identity_id else None,
                    "email": row.email,
                    "username": row.username,
                    "tenant_id": str(row.tenant_id) if row.tenant_id else None,
                }
                for row in group.identities
            ],
        }
        for group in groups
    ]


def projected_identity_foundation(user: User) -> IdentityFoundation:
    normalized = normalize_email(user.email or user.username)
    if user.security_status:
        status = user.security_status
    elif not user.is_active:
        status = User.SecurityStatus.LOCKED
    elif user.mfa_enabled:
        status = User.SecurityStatus.ACTIVE
    else:
        status = User.SecurityStatus.MFA_SETUP_REQUIRED
    return IdentityFoundation(
        identity_id=user.identity_id or uuid.uuid4(),
        normalized_email=normalized,
        security_version=user.security_version or 1,
        security_status=status,
        identity_legal_hold=bool(user.identity_legal_hold),
        changed=False,
    )


def ensure_identity_foundation(user: User, *, using: str = "default") -> IdentityFoundation:
    projected = projected_identity_foundation(user)
    changed = IdentitySecurityRepository.initialize_legal_hold_default(identity=user, using=using)
    if changed:
        user.refresh_from_db(using=using)
    updates: dict[str, object] = {}
    if user.identity_id is None:
        updates["identity_id"] = projected.identity_id
    if user.normalized_email is None:
        updates["normalized_email"] = projected.normalized_email.value
    if user.email_normalization_version is None:
        updates["email_normalization_version"] = projected.normalized_email.version
    if user.security_version is None:
        updates["security_version"] = projected.security_version
    if user.security_status is None:
        updates["security_status"] = projected.security_status
    if updates:
        IdentitySecurityRepository.initialize_foundation(identity=user, updates=updates, using=using)
        user.refresh_from_db(using=using)
    return IdentityFoundation(
        identity_id=user.identity_id,
        normalized_email=NormalizedEmail(user.normalized_email, user.email_normalization_version),
        security_version=user.security_version,
        security_status=user.security_status,
        identity_legal_hold=bool(user.identity_legal_hold),
        changed=changed or bool(updates),
    )
