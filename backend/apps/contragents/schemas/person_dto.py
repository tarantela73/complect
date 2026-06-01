import uuid

from pydantic import BaseModel, field_validator


class PersonResponseSchema(BaseModel):
    id: uuid.UUID
    first_name: str
    last_name: str
    middle_name: str
    masked_passport: str
    masked_phone: str

    @field_validator("masked_passport", mode="before")
    @classmethod
    def mask_passport_data(cls, v: str) -> str:
        """Превращает '4510-777888' в '45**-***888' для UI-безопасности."""
        if len(v) >= 10:
            return f"{v[:2]}**-***{v[-3:]}"
        return "****-******"

    @field_validator("masked_phone", mode="before")
    @classmethod
    def mask_phone_data(cls, v: str) -> str:
        """Превращает '+79991234567' в '+7(999)***-45-67'."""
        if len(v) >= 11:
            return f"{v[:2]}({v[2:5]})***-{v[8:10]}-{v[10:]}"
        return "+7(***)***-**-**"
