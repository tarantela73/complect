from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar


@dataclass(slots=True, frozen=True)
class NotificationContext:
    event_type: str
    tenant_id: uuid.UUID
    payload: dict


class BaseNotificationHandler(ABC):
    event_type: ClassVar[str]
    default_email_enabled: ClassVar[bool] = False
    default_in_app_enabled: ClassVar[bool] = True

    def get_recipient_ids(self, ctx: NotificationContext) -> list[uuid.UUID]:
        uid_str = ctx.payload.get("user_id")
        if uid_str:
            try:
                return [uuid.UUID(str(uid_str))]
            except (ValueError, AttributeError):
                pass
        return []

    @abstractmethod
    def get_title(self, ctx: NotificationContext) -> str: ...

    @abstractmethod
    def get_body(self, ctx: NotificationContext) -> str: ...
