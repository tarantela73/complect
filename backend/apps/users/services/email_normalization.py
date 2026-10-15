import unicodedata
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

EMAIL_NORMALIZATION_VERSION = 1
RESERVED_MERGE_DOMAIN = "identity.invalid"


@dataclass(frozen=True, slots=True)
class NormalizedEmail:
    value: str
    version: int


def normalize_email(email: str) -> NormalizedEmail:
    value = unicodedata.normalize("NFKC", email.strip()).casefold()
    if len(value) > 254:
        raise ValidationError("EMAIL_TOO_LONG")
    try:
        validate_email(value)
    except ValidationError:
        # Django's validator deliberately limits the local part to ASCII,
        # while the frozen IAM candidate format accepts normalized SMTPUTF8
        # local parts.  Keep Django as the domain/syntax authority and narrow
        # this exception to a structurally valid non-ASCII local part.
        if value.count("@") != 1:
            raise
        local, domain = value.rsplit("@", 1)
        if (
            local.isascii()
            or not local
            or len(local) > 64
            or any(character.isspace() for character in local)
            or local.startswith(".")
            or local.endswith(".")
            or ".." in local
        ):
            raise
        validate_email(f"identity@{domain}")
    return NormalizedEmail(value=value, version=EMAIL_NORMALIZATION_VERSION)


def is_reserved_merge_email(email: str) -> bool:
    try:
        normalized = normalize_email(email).value
    except ValidationError:
        return False
    return normalized.rsplit("@", 1)[-1] == RESERVED_MERGE_DOMAIN
