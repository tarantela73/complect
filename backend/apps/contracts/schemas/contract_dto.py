import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class ContractCreateRequestSchema(BaseModel):
    contract_number: str = Field(..., max_length=100)
    title: str = Field(..., max_length=255)
    resource_type: str = Field(..., description="ELECTRICITY, GAS, WATER, HEAT, OTHER")
    total_amount: int = Field(..., description="Общая сумма договора в копейках", gt=0)
    contragent_id: Optional[uuid.UUID] = None


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
    contragent_id: Optional[uuid.UUID] = None

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


class LastPaymentSchema(BaseModel):
    transaction_id: uuid.UUID
    amount: int
    value_date: Optional[date] = None


class ContractDetailSchema(ContractResponseSchema):
    last_payments: list[LastPaymentSchema] = []
    attachments_count: int = 0
    files_available: int = 0
    files_quarantined: int = 0
    files_unresolvable: int = Field(0, ge=0)


class ContractListResponse(BaseModel):
    results: list[ContractResponseSchema]
    next_cursor: Optional[str] = None
    count: int


class ContractCloseRequest(BaseModel):
    pass


class ContractEventLogEntrySchema(BaseModel):
    event_id: uuid.UUID
    event_type: str
    label: str
    occurred_at: str
    user_id: Optional[uuid.UUID] = None
    correlation_id: uuid.UUID


class ContractEventLogResponse(BaseModel):
    results: list[ContractEventLogEntrySchema]
    count: int
