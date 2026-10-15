from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class UserDisplayDTO:
    user_id: uuid.UUID
    full_name: str


@runtime_checkable
class UserInfoProtocol(Protocol):
    """[ADR-0005] Cross-domain read contract: display names for user UUIDs (Query Side)."""

    def get_users_display(
        self,
        ids: list[uuid.UUID],
        tenant_id: uuid.UUID,
    ) -> dict[uuid.UUID, UserDisplayDTO]: ...
