# backend/apps/common/services/outbox_service.py
import uuid
from dataclasses import asdict

from apps.common.models.outbox import OutboxRecord
from apps.common.tenancy.context import get_current_tenant_id


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

        return OutboxRecord.objects.create(
            event_type=event.__class__.__name__,
            payload_json=payload,
            correlation_id=event.correlation_id,
        )
