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
_INFLIGHT_CACHE_KEY = "outbox:dispatch:inflight"
_WAKEUP_COALESCE_TTL: int = getattr(settings, "OUTBOX_WAKEUP_COALESCE_TTL", 2)
# Must exceed max expected task duration. Safety net if task crashes without cleanup.
_INFLIGHT_GUARD_TTL: int = getattr(settings, "OUTBOX_INFLIGHT_GUARD_TTL", 30)


class TransactionalOutboxService:
    @staticmethod
    def push_to_outbox(
        event,
        *,
        causal_root_id: uuid.UUID | None = None,
    ) -> OutboxRecord:
        """
        Write a domain event to the Transactional Outbox within the current transaction.

        causal_root_id — pass the original OutboxRecord.id when creating a repair or
        compensating event so the causal chain remains traceable (ADR-0015 Invariant 3).
        Omit for normal flow: causal_root_id defaults to the new record's own id.
        """
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

        record_id = uuid.uuid4()
        record = OutboxRecord.objects.create(
            id=record_id,
            event_type=event.__class__.__name__,
            payload_json=payload,
            correlation_id=event.correlation_id,
            causal_root_id=causal_root_id if causal_root_id is not None else record_id,
        )

        # Fast-path dispatch with two-level guard:
        # 1. wakeup_key (TTL=2s): debounce — at most one wake-up per burst window.
        # 2. inflight_key (TTL=30s): at-least-once guarantee — if the task crashes
        #    before clearing the inflight key, the TTL expires and the next insert
        #    can trigger a new wake-up. Without this, a crashed task silently eats
        #    the coalesce window: process A sets wakeup, task crashes, process B
        #    sees wakeup still set → no dispatch → silent loss until Beat fires.
        def _fire_dispatcher() -> None:
            try:
                from apps.common.tasks.outbox_dispatcher import (
                    run_outbox_polling_dispatcher_task,
                )
                if cache.add(_WAKEUP_CACHE_KEY, 1, timeout=_WAKEUP_COALESCE_TTL):
                    if cache.add(_INFLIGHT_CACHE_KEY, 1, timeout=_INFLIGHT_GUARD_TTL):
                        try:
                            run_outbox_polling_dispatcher_task.apply_async(countdown=0)
                        except Exception:
                            cache.delete(_INFLIGHT_CACHE_KEY)
                            raise
            except Exception as exc:
                logger.warning("outbox_dispatcher_wakeup_failed", error=str(exc))

        transaction.on_commit(_fire_dispatcher)

        return record
