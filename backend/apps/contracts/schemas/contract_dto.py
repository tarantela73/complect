import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class ContractCreateRequestSchema(BaseModel):
    contract_number: str = Field(..., max_length=100)
    title: str = Field(..., max_length=255)
    resource_type: str = Field(..., description="ELECTRICITY, GAS, WATER, HEAT, OTHER")
    total_amount: int = Field(..., description="Общая сумма договора в копейках", gt=0)


class ContractUpdateSchema(BaseModel):
    title: Optional[str] = Field(None, max_length=255)
    total_amount: Optional[int] = Field(None, description="Общая сумма договора в копейках", gt=0)


class ContractResponseSchema(BaseModel):
    id: uuid.UUID
    contract_number: str
    title: str
    status: str
    resource_type: str
    total_amount: int
    paid_amount: int
    remaining_amount: int = 0
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def compute_remaining(cls, values):
        if isinstance(values, dict):
            total = values.get("total_amount", 0)
            paid = values.get("paid_amount", 0)
            if "remaining_amount" not in values:
                values["remaining_amount"] = total - paid
        return values


class ContractListResponse(BaseModel):
    results: list[ContractResponseSchema]
    next_cursor: Optional[str] = None
    count: int


class ContractCloseRequest(BaseModel):
    pass
