"""
CQRS Query Side — system-wide Outbox health metrics.
Read-only. No tenant scoping — operational visibility is cross-tenant by design.
"""
from apps.common.models.outbox import OutboxRecord


def get_outbox_health() -> dict[str, int]:
    pending = OutboxRecord.objects.filter(
        is_processed=False,
        failed_permanently_at__isnull=True,
    ).count()
    failed = OutboxRecord.objects.filter(
        failed_permanently_at__isnull=False,
    ).count()
    return {"outbox_pending": pending, "outbox_failed": failed}
