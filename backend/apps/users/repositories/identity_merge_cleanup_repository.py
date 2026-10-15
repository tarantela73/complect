from django.db.models import F
from django.utils import timezone

from apps.users.models import IdentityMergeOperation, IdentityMergeRedisCleanupRecord


class IdentityMergeCleanupRepository:
    @staticmethod
    def ensure_pending(
        *,
        operation: IdentityMergeOperation,
        actor_user_pks: list[int],
        using: str = "default",
    ) -> IdentityMergeRedisCleanupRecord:
        record, _created = IdentityMergeRedisCleanupRecord.objects.using(using).get_or_create(
            operation=operation,
            defaults={"actor_user_pks": sorted(set(actor_user_pks))},
        )
        return record

    @staticmethod
    def record_failure(record: IdentityMergeRedisCleanupRecord, code: str, *, using: str = "default") -> None:
        IdentityMergeRedisCleanupRecord.objects.using(using).filter(
            pk=record.pk,
            version=record.version,
            status=IdentityMergeRedisCleanupRecord.Status.PENDING,
            is_legal_hold=False,
        ).update(
            attempt_count=F("attempt_count") + 1,
            last_failure_code=code,
            version=F("version") + 1,
            updated_at=timezone.now(),
        )

    @staticmethod
    def complete(record: IdentityMergeRedisCleanupRecord, *, using: str = "default") -> bool:
        return (
            IdentityMergeRedisCleanupRecord.objects.using(using).filter(
                pk=record.pk,
                version=record.version,
                status=IdentityMergeRedisCleanupRecord.Status.PENDING,
                is_legal_hold=False,
            ).update(
                status=IdentityMergeRedisCleanupRecord.Status.COMPLETED,
                completed_at=timezone.now(),
                last_failure_code="",
                version=F("version") + 1,
                updated_at=timezone.now(),
            )
            == 1
        )
