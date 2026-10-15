from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import ClassVar


@dataclass(slots=True, frozen=True)
class ContragentCreatedEvent:
    EVENT_TYPE: ClassVar[str] = "contragents.ContragentCreated"
    event_id: uuid.UUID
    correlation_id: uuid.UUID
    occurred_at: datetime
    contragent_id: uuid.UUID
    user_id: uuid.UUID
    name: str
    inn: str
    event_version: int = 1

    @classmethod
    def create(
        cls,
        correlation_id: uuid.UUID,
        contragent_id: uuid.UUID,
        user_id: uuid.UUID,
        name: str,
        inn: str,
    ) -> "ContragentCreatedEvent":
        return cls(
            event_id=uuid.uuid4(),
            correlation_id=correlation_id,
            occurred_at=datetime.now(tz=timezone.utc),
            contragent_id=contragent_id,
            user_id=user_id,
            name=name,
            inn=inn,
        )


@dataclass(slots=True, frozen=True)
class ContragentUpdatedEvent:
    EVENT_TYPE: ClassVar[str] = "contragents.ContragentUpdated"
    event_id: uuid.UUID
    correlation_id: uuid.UUID
    occurred_at: datetime
    contragent_id: uuid.UUID
    user_id: uuid.UUID
    name: str
    event_version: int = 1

    @classmethod
    def create(
        cls,
        correlation_id: uuid.UUID,
        contragent_id: uuid.UUID,
        user_id: uuid.UUID,
        name: str,
    ) -> "ContragentUpdatedEvent":
        return cls(
            event_id=uuid.uuid4(),
            correlation_id=correlation_id,
            occurred_at=datetime.now(tz=timezone.utc),
            contragent_id=contragent_id,
            user_id=user_id,
            name=name,
        )


@dataclass(slots=True, frozen=True)
class ContragentDeletedEvent:
    EVENT_TYPE: ClassVar[str] = "contragents.ContragentDeleted"
    event_id: uuid.UUID
    correlation_id: uuid.UUID
    occurred_at: datetime
    contragent_id: uuid.UUID
    user_id: uuid.UUID
    event_version: int = 1

    @classmethod
    def create(
        cls,
        correlation_id: uuid.UUID,
        contragent_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> "ContragentDeletedEvent":
        return cls(
            event_id=uuid.uuid4(),
            correlation_id=correlation_id,
            occurred_at=datetime.now(tz=timezone.utc),
            contragent_id=contragent_id,
            user_id=user_id,
        )


@dataclass(slots=True, frozen=True)
class ContragentFileAttachedEvent:
    EVENT_TYPE: ClassVar[str] = "contragents.ContragentFileAttached"
    event_id: uuid.UUID
    correlation_id: uuid.UUID
    occurred_at: datetime
    contragent_id: uuid.UUID
    user_id: uuid.UUID
    file_id: uuid.UUID
    event_version: int = 1

    @classmethod
    def create(
        cls,
        correlation_id: uuid.UUID,
        contragent_id: uuid.UUID,
        user_id: uuid.UUID,
        file_id: uuid.UUID,
    ) -> "ContragentFileAttachedEvent":
        return cls(
            event_id=uuid.uuid4(),
            correlation_id=correlation_id,
            occurred_at=datetime.now(tz=timezone.utc),
            contragent_id=contragent_id,
            user_id=user_id,
            file_id=file_id,
        )


@dataclass(slots=True, frozen=True)
class ContragentFileDetachedEvent:
    EVENT_TYPE: ClassVar[str] = "contragents.ContragentFileDetached"
    event_id: uuid.UUID
    correlation_id: uuid.UUID
    occurred_at: datetime
    contragent_id: uuid.UUID
    user_id: uuid.UUID
    file_id: uuid.UUID
    event_version: int = 1

    @classmethod
    def create(
        cls,
        correlation_id: uuid.UUID,
        contragent_id: uuid.UUID,
        user_id: uuid.UUID,
        file_id: uuid.UUID,
    ) -> "ContragentFileDetachedEvent":
        return cls(
            event_id=uuid.uuid4(),
            correlation_id=correlation_id,
            occurred_at=datetime.now(tz=timezone.utc),
            contragent_id=contragent_id,
            user_id=user_id,
            file_id=file_id,
        )


@dataclass(slots=True, frozen=True)
class ContragentPersonProfileCreatedEvent:
    EVENT_TYPE: ClassVar[str] = "contragents.PersonProfileCreated"
    event_id: uuid.UUID
    correlation_id: uuid.UUID
    occurred_at: datetime
    contragent_id: uuid.UUID
    user_id: uuid.UUID
    profile_id: uuid.UUID
    event_version: int = 1

    @classmethod
    def create(
        cls,
        correlation_id: uuid.UUID,
        contragent_id: uuid.UUID,
        user_id: uuid.UUID,
        profile_id: uuid.UUID,
    ) -> "ContragentPersonProfileCreatedEvent":
        return cls(
            event_id=uuid.uuid4(),
            correlation_id=correlation_id,
            occurred_at=datetime.now(tz=timezone.utc),
            contragent_id=contragent_id,
            user_id=user_id,
            profile_id=profile_id,
        )
