from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class TaskCreateIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=10000)
    priority: str = Field(default="normal", description="low | normal | high")
    assigned_to_id: Optional[uuid.UUID] = None
    due_date: Optional[date] = None
    entity_type: str = Field(default="", max_length=50)
    entity_id: Optional[uuid.UUID] = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Заголовок не может быть пустым.")
        return v


class TaskUpdateIn(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=10000)
    priority: Optional[str] = Field(None, description="low | normal | high")
    assigned_to_id: Optional[uuid.UUID] = None
    due_date: Optional[date] = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("Заголовок не может быть пустым.")
        return v


class TaskStatusChangeIn(BaseModel):
    status: str


class TaskAttachFileIn(BaseModel):
    file_id: uuid.UUID


class TaskOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    status: str
    priority: str
    created_by: uuid.UUID
    assigned_to_id: Optional[uuid.UUID] = None
    due_date: Optional[date] = None
    entity_type: str
    entity_id: Optional[uuid.UUID] = None
    closed_at: Optional[datetime] = None
    closed_by_id: Optional[uuid.UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_legal_hold: bool = False
    version: int = 1

    model_config = {"from_attributes": True}


class TaskListOut(BaseModel):
    items: list[TaskOut]
    next_cursor: Optional[uuid.UUID] = None


class TaskAttachmentOut(BaseModel):
    id: uuid.UUID
    file_id: uuid.UUID
    attached_by: uuid.UUID
    created_at: Optional[datetime] = None


class TaskCommentIn(BaseModel):
    body: str = Field(..., max_length=10000)


class TaskCommentOut(BaseModel):
    id: uuid.UUID
    body: str
    author_id: uuid.UUID
    created_at: Optional[datetime] = None
