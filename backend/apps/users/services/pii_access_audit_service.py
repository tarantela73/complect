import hashlib
import hmac
from collections.abc import Iterable

from django.conf import settings
from django.db import DatabaseError, connections, transaction
from django.utils import timezone

from apps.users.models import IdentitySecurityAuditRecord


class PiiAuditUnavailable(RuntimeError):  # noqa: N818 - public plan contract
    """Closed failure raised when audit persistence cannot be guaranteed."""


class PiiAccessAuditService:
    @staticmethod
    def pseudonymous_user_subject(user_pk: int) -> str:
        return hmac.new(
            settings.SECRET_KEY.encode(),
            f"identity-user-pk:{user_pk}".encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def append_batch_and_commit(
        *,
        subjects: Iterable[object],
        actor_type: str,
        actor_ref: str,
        reason_code: str,
        request_id,
        correlation_id,
        using: str = "default",
    ) -> None:
        """Commit audit before the caller may read or export the subjects' PII.

        The selected connection must be in autocommit, outside any atomic block
        (including test wrappers). A savepoint release cannot satisfy this
        contract. Reject before consuming subjects; never commit caller work.
        """
        if actor_type not in {"operator", "system", "user"} or not actor_ref.strip():
            raise PiiAuditUnavailable("PII audit unavailable")
        try:
            if connections[using].in_atomic_block or not connections[using].get_autocommit():
                raise PiiAuditUnavailable("PII audit requires a dedicated transaction")
            rows = []
            for subject in subjects:
                user_pk = int(subject.user_pk)
                subject_hmac = PiiAccessAuditService.pseudonymous_user_subject(user_pk)
                rows.append(
                    IdentitySecurityAuditRecord(
                        identity_id=subject.identity_id,
                        event_type="pii.identity_email_read",
                        actor_type=actor_type,
                        actor_ref=actor_ref,
                        reason_code=reason_code,
                        subject_hmac=subject_hmac,
                        request_id=request_id,
                        correlation_id=correlation_id,
                        metadata={"subject_scheme": "hmac-sha256:identity-user-pk:v1"},
                        occurred_at=timezone.now(),
                    ),
                )
            with transaction.atomic(using=using, durable=True):
                IdentitySecurityAuditRecord.objects.using(using).bulk_create(rows)
        except (DatabaseError, TypeError, ValueError) as exc:
            raise PiiAuditUnavailable("PII audit unavailable") from exc
