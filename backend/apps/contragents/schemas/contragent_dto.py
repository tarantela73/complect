import uuid
from typing import Optional

from pydantic import BaseModel, Field


class ContragentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    inn: str = Field(min_length=10, max_length=12)
    contragent_type: str = Field(description="LEGAL_ENTITY, ENTREPRENEUR, INDIVIDUAL")


class ContragentResponse(BaseModel):
    id: uuid.UUID
    name: str
    inn: str
    contragent_type: str

    model_config = {"from_attributes": True}


class ContragentListResponse(BaseModel):
    results: list[ContragentResponse]
    next_cursor: Optional[str] = None
    count: int


class PersonCreateRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    middle_name: str = Field(default="", max_length=100)
    passport: str = Field(min_length=9, max_length=20, description="Серия и номер паспорта")
    snils: str = Field(min_length=11, max_length=14, description="СНИЛС")
    phone: str = Field(min_length=10, max_length=15, description="Телефон в формате +79991234567")


class PersonListResponse(BaseModel):
    results: list[dict]
    count: int
