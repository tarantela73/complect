"""
CQRS Query Side — read projection of Transactional Outbox for contract lifecycle events.
No writes, no business logic, no domain model imports.
"""
from __future__ import annotations

import uuid
from typing import Optional

from apps.common.models.outbox import OutboxRecord
from apps.contracts.schemas.contract_dto import ContractEventLogEntrySchema, ContractEventLogResponse

_CONTRACT_EVENT_TYPES = frozenset({
    "contracts.ContractCreated",
    "contracts.ContractActivated",
    "contracts.ContractClosed",
})

_EVENT_LABELS: dict[str, str] = {
    "contracts.ContractCreated": "Создан",
    "contracts.ContractActivated": "Активирован",
    "contracts.ContractClosed": "Закрыт",
}


def get_contract_event_log(contract_id: uuid.UUID, tenant_id: uuid.UUID) -> ContractEventLogResponse:
    qs = (
        OutboxRecord.objects.filter(
            event_type__in=_CONTRACT_EVENT_TYPES,
            payload_json__contract_id=str(contract_id),
            payload_json__tenant_id=str(tenant_id),
        )
        .order_by("created_at")
        .only("id", "event_type", "payload_json", "correlation_id", "created_at")
    )

    entries: list[ContractEventLogEntrySchema] = []
    for record in qs:
        payload = record.payload_json
        user_id_raw: Optional[str] = payload.get("user_id")
        entries.append(
            ContractEventLogEntrySchema(
                event_id=record.id,
                event_type=record.event_type,
                label=_EVENT_LABELS.get(record.event_type, record.event_type),
                occurred_at=payload.get("occurred_at") or record.created_at.isoformat(),
                user_id=uuid.UUID(user_id_raw) if user_id_raw else None,
                correlation_id=record.correlation_id,
            )
        )

    return ContractEventLogResponse(results=entries, count=len(entries))
