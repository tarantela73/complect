import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.conf import settings


def canonical_action_bytes(action: dict[str, Any]) -> bytes:
    return json.dumps(action, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def canonical_action_digest(action: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_action_bytes(action)).hexdigest()


def decode_signature(value: str | bytes) -> bytes:
    if isinstance(value, bytes):
        return value
    try:
        return bytes.fromhex(value)
    except ValueError:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)


@dataclass(frozen=True, slots=True)
class ReconciliationApproval:
    reference: str
    key_id: str
    signature: str | bytes
    action: dict[str, Any] | None = None


class ReconciliationApprovalVerifier:
    @staticmethod
    def _keyring() -> dict[str, str | bytes]:
        configured = getattr(settings, "IAM_RECONCILIATION_APPROVAL_PUBLIC_KEYS", {})
        if isinstance(configured, str):
            configured = json.loads(configured or "{}")
        return configured

    @classmethod
    def verify(cls, approval: ReconciliationApproval, action: dict[str, Any]) -> bool:
        key_value = cls._keyring().get(approval.key_id)
        if key_value is None:
            return False
        if isinstance(key_value, str):
            try:
                key_bytes = bytes.fromhex(key_value)
            except ValueError:
                key_bytes = base64.b64decode(key_value)
        else:
            key_bytes = key_value
        try:
            Ed25519PublicKey.from_public_bytes(key_bytes).verify(
                decode_signature(approval.signature),
                canonical_action_bytes(action),
            )
        except (InvalidSignature, ValueError, TypeError):
            return False
        return True
