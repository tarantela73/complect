from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ObjectCreationDTO:
    name: str
    address: str
    resource_type: str
    object_type: str = "BUILDING"
    contragent_id: uuid.UUID | None = None
    source_request_id: uuid.UUID | None = None


@runtime_checkable
class ObjectCreatorProtocol(Protocol):
    """[ADR-0005] Cross-domain write contract: apps/requests → apps/objects (Command Side)."""

    def create_supply_object(self, data: ObjectCreationDTO) -> uuid.UUID: ...
