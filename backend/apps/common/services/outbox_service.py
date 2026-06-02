import uuid
from dataclasses import asdict

import structlog
from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from apps.common.models.outbox import OutboxRecord
from apps.common.tenancy.context import get_current_tenant_id

logger = structlog.get_logger(__name__)

_WAKEUP_CACHE_KEY = "outbox:dispatch:wakeup"
_WAKEUP_COALESCE_TTL: int = getattr(settings, "OUTBOX_WAKEUP_COALESCE_TTL", 2)


class TransactionalOutboxService:
    @staticmethod
    def push_to_outbox(event) -> OutboxRecord:
        try:
            tenant_id = get_current_tenant_id()
        except Exception:
            tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000000")

        event_dict = asdict(event)
        payload = {
            k: str(v) if isinstance(v, uuid.UUID) else v.isoformat() if hasattr(v, "isoformat") else v
            for k, v in event_dict.items()
        }
        payload["tenant_id"] = str(tenant_id)

        record = OutboxRecord.objects.create(
            event_type=event.__class__.__name__,
            payload_json=payload,
            correlation_id=event.correlation_id,
        )

        # Fast-path dispatch: one wake-up per COALESCE_TTL window.
        # cache.add() is SETNX — only the first on_commit in a burst wins the lock.
        # Beat schedule (10s) remains the fallback for orphaned records.
        def _fire_dispatcher() -> None:
            try:
                from apps.common.tasks.outbox_dispatcher import (
                    run_outbox_polling_dispatcher_task,
                )
                if cache.add(_WAKEUP_CACHE_KEY, 1, timeout=_WAKEUP_COALESCE_TTL):
                    run_outbox_polling_dispatcher_task.apply_async(countdown=0)
            except Exception as exc:
                logger.warning("outbox_dispatcher_wakeup_failed", error=str(exc))

        transaction.on_commit(_fire_dispatcher)

        return record
