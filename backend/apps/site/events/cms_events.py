from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar


@dataclass(slots=True, frozen=True)
class PagePublishedEvent:
    EVENT_TYPE: ClassVar[str] = "cms.PagePublished"

    event_id: uuid.UUID
    event_version: int
    occurred_at: datetime
    correlation_id: uuid.UUID
    tenant_id: uuid.UUID
    page_slug: str
    event_type: str = field(default="cms.PagePublished", init=False)
