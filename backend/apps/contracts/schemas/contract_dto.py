import uuid

from pydantic import BaseModel, Field, model_validator


class ContractCreateRequestSchema(BaseModel):
    contract_number: str = Field(..., max_length=100)
    title: str = Field(..., max_length=255)
    resource_type: str = Field(..., description="ELECTRICITY, GAS, WATER, HEAT, OTHER")
    total_amount: int = Field(..., description="Общая сумма договора в копейках")


class ContractResponseSchema(BaseModel):
    id: uuid.UUID
    contract_number: str
    title: str
    status: str
    resource_type: str
    total_amount: int
    paid_amount: int
    remaining_amount: int = 0

    @model_validator(mode="before")
    @classmethod
    def compute_remaining(cls, values):
        if isinstance(values, dict):
            total = values.get("total_amount", 0)
            paid = values.get("paid_amount", 0)
            if "remaining_amount" not in values:
                values["remaining_amount"] = total - paid
        return values
