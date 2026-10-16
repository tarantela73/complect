"""Immutable provenance event for a compatibility authority decision."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class LegacyDocumentMappingChanged:
    EVENT_TYPE: ClassVar[str] = "contracts.LegacyDocumentMappingChanged"
    event_id: uuid.UUID
    event_version: int
    occurred_at: datetime
    correlation_id: uuid.UUID
    mapping_id: uuid.UUID
    legacy_document_id: uuid.UUID
    target_file_id: uuid.UUID | None
    proposed_target_file_id: uuid.UUID
    actor_id: uuid.UUID
    mapping_state: str
    mapping_version: int
    proposed_snapshot_sha256: str

    @classmethod
    def create(
        cls,
        *,
        correlation_id: uuid.UUID,
        mapping_id: uuid.UUID,
        legacy_document_id: uuid.UUID,
        target_file_id: uuid.UUID | None,
        proposed_target_file_id: uuid.UUID,
        actor_id: uuid.UUID,
        mapping_state: str,
        mapping_version: int,
        proposed_snapshot_sha256: str,
    ) -> LegacyDocumentMappingChanged:
        return cls(
            uuid.uuid4(),
            1,
            datetime.now(timezone.utc),
            correlation_id,
            mapping_id,
            legacy_document_id,
            target_file_id,
            proposed_target_file_id,
            actor_id,
            mapping_state,
            mapping_version,
            proposed_snapshot_sha256,
        )
