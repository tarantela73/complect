"""
CQRS Query Side — read projection of Transactional Outbox for contragent Historia.
No writes, no business logic.
"""
from __future__ import annotations

import uuid
from typing import Optional

from apps.common.models.outbox import OutboxRecord
from apps.contragents.schemas.attachment_dto import (
    ContragentTimelineEntrySchema,
    ContragentTimelineResponse,
)

_CONTRAGENT_EVENT_TYPES = frozenset({
    "contragents.ContragentCreated",
    "contragents.ContragentUpdated",
    "contragents.ContragentDeleted",
    "contragents.ContragentFileAttached",
    "contragents.ContragentFileDetached",
    "contragents.PersonProfileCreated",
})

_EVENT_LABELS: dict[str, str] = {
    "contragents.ContragentCreated": "Создан",
    "contragents.ContragentUpdated": "Изменён",
    "contragents.ContragentDeleted": "Удалён",
    "contragents.ContragentFileAttached": "Файл прикреплён",
    "contragents.ContragentFileDetached": "Файл откреплён",
    "contragents.PersonProfileCreated": "Профиль физлица добавлен",
}


def get_contragent_timeline(
    contragent_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> ContragentTimelineResponse:
    qs = (
        OutboxRecord.objects.filter(
            event_type__in=_CONTRAGENT_EVENT_TYPES,
            payload_json__contragent_id=str(contragent_id),
            payload_json__tenant_id=str(tenant_id),
        )
        .order_by("-created_at")
        .only("id", "event_type", "payload_json", "correlation_id", "created_at")
    )

    entries: list[ContragentTimelineEntrySchema] = []
    for record in qs:
        payload = record.payload_json
        user_id_raw: Optional[str] = payload.get("user_id")
        corr_raw: Optional[str] = payload.get("correlation_id")
        entries.append(
            ContragentTimelineEntrySchema(
                event_id=record.id,
                event_type=record.event_type,
                label=_EVENT_LABELS.get(record.event_type, record.event_type),
                occurred_at=payload.get("occurred_at") or record.created_at.isoformat(),
                user_id=uuid.UUID(user_id_raw) if user_id_raw else None,
                correlation_id=uuid.UUID(corr_raw) if corr_raw else None,
            )
        )

    return ContragentTimelineResponse(results=entries, count=len(entries))
