from pydantic import BaseModel, Field


class RawOnesPaymentInputSchema(BaseModel):
    """Сырой входящий контракт данных от 1С (Внешний грязный язык)."""

    DocGUID: str = Field(..., description="Уникальный идентификатор документа в 1С")
    OrganizationINN: str = Field(..., description="ИНН нашей организации")
    SummaDoc: float = Field(
        ..., description="Сумма в рублях, приходящая от внешней системы"
    )
    NaznacheniePlat: str = Field(..., description="Текстовое назначение платежа")


class InternalLedgerTransactionDTO(BaseModel):
    """Стерильный контракт данных для финансового леджера ERP (Внутренний чистый язык)."""

    external_ref_id: str
    tenant_inn: str
    amount_in_cents: int  # Конвертировано в копейки для исключения float-багов
    description: str

