import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.conf import settings

from apps.users.security.credential_entrypoints import credential_entrypoint_manifest_digest


class DeferredMergeExecutionDisabled(RuntimeError):  # noqa: N818 - public plan contract
    pass


@dataclass(frozen=True, slots=True)
class DeferredMergeExecutionEvaluation:
    effective: bool
    reason: str


class DeferredMergeExecutionPolicy:
    PURPOSE = "complect-iam-deferred-merge-coverage-v1"

    @staticmethod
    def _parse_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        return parsed.astimezone(UTC)

    @classmethod
    def evaluate(cls, *, now: datetime | None = None) -> DeferredMergeExecutionEvaluation:
        if not getattr(settings, "IAM_DEFERRED_MERGE_EXECUTION_ENABLED", False):
            return DeferredMergeExecutionEvaluation(False, "BOOLEAN_DISABLED")
        now = now or datetime.now(UTC)
        try:
            approved_at = cls._parse_time(settings.IAM_DEFERRED_MERGE_COVERAGE_APPROVED_AT)
            expires_at = cls._parse_time(settings.IAM_DEFERRED_MERGE_COVERAGE_EXPIRES_AT)
            if approved_at > now or expires_at <= now or expires_at > approved_at + timedelta(days=7):
                return DeferredMergeExecutionEvaluation(False, "ATTESTATION_EXPIRED")
            manifest_digest = credential_entrypoint_manifest_digest()
            if settings.IAM_DEFERRED_MERGE_COVERAGE_DIGEST != manifest_digest:
                return DeferredMergeExecutionEvaluation(False, "MANIFEST_MISMATCH")
            build_commit = getattr(settings, "BUILD_COMMIT", "")
            if not build_commit or settings.IAM_DEFERRED_MERGE_COVERAGE_COMMIT != build_commit:
                return DeferredMergeExecutionEvaluation(False, "BUILD_COMMIT_MISMATCH")
            envelope = {
                "purpose": cls.PURPOSE,
                "environment": settings.ENVIRONMENT,
                "schema_version": 1,
                "scanner_version": 1,
                "manifest_sha256": manifest_digest,
                "build_commit": build_commit,
                "approved_at": settings.IAM_DEFERRED_MERGE_COVERAGE_APPROVED_AT,
                "expires_at": settings.IAM_DEFERRED_MERGE_COVERAGE_EXPIRES_AT,
                "nonce": settings.IAM_DEFERRED_MERGE_COVERAGE_NONCE,
            }
            keyring = settings.IAM_DEFERRED_MERGE_COVERAGE_PUBLIC_KEYS
            if isinstance(keyring, str):
                keyring = json.loads(keyring or "{}")
            raw_key = keyring[settings.IAM_DEFERRED_MERGE_COVERAGE_KEY_ID]
            key_bytes = base64.b64decode(raw_key) if isinstance(raw_key, str) else raw_key
            signature_value = settings.IAM_DEFERRED_MERGE_COVERAGE_SIGNATURE
            try:
                signature = bytes.fromhex(signature_value)
            except ValueError:
                signature = base64.b64decode(signature_value)
            message = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
            Ed25519PublicKey.from_public_bytes(key_bytes).verify(signature, message)
        except KeyError:
            return DeferredMergeExecutionEvaluation(False, "UNKNOWN_KEY")
        except InvalidSignature:
            return DeferredMergeExecutionEvaluation(False, "INVALID_SIGNATURE")
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            return DeferredMergeExecutionEvaluation(False, "INVALID_ATTESTATION")
        return DeferredMergeExecutionEvaluation(True, "ENABLED")

    @classmethod
    def require_enabled(cls) -> None:
        evaluation = cls.evaluate()
        if not evaluation.effective:
            raise DeferredMergeExecutionDisabled(evaluation.reason)
