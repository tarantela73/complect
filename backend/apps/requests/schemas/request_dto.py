import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class RequestCreateRequest(BaseModel):
    client_name: str
    client_phone: str
    client_email: str = ""
    address: str
    resource_type: str
    comment: str = ""
    source: str = "MANUAL"
    manager_id: Optional[uuid.UUID] = None


class RequestAssignRequest(BaseModel):
    manager_id: uuid.UUID


class RequestResponse(BaseModel):
    id: uuid.UUID
    client_name: str
    client_phone: str
    client_email: str
    address: str
    resource_type: str
    comment: str
    source: str
    status: str
    manager_id: Optional[uuid.UUID] = None
    manager_name: Optional[str] = None
    converted_object_id: Optional[uuid.UUID] = None
    converted_at: Optional[datetime] = None
    created_at: datetime


class RequestListResponse(BaseModel):
    results: list[RequestResponse]
    next_cursor: Optional[str] = None
    count: int


class ConvertResponse(BaseModel):
    object_id: uuid.UUID
