import re
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
        if v == "****-******" or re.fullmatch(r"[^\W_]{2}\*{2}-\*{3}[^\W_]{3}", v):
            return v
        if len(v) >= 10:
            return f"{v[:2]}**-***{v[-3:]}"
        return "****-******"

    @field_validator("masked_phone", mode="before")
    @classmethod
    def mask_phone_data(cls, v: str) -> str:
        """Превращает '+79991234567' в '+7(999)***-45-67'."""
        if v == "+7(***)***-**-**" or re.fullmatch(r"\+\d\(\d{3}\)\*{3}-\d{2}-\d{2}", v):
            return v
        digits = "".join(ch for ch in v if ch.isdigit())
        if len(digits) >= 11:
            return f"+{digits[0]}({digits[1:4]})***-{digits[-4:-2]}-{digits[-2:]}"
        return "+7(***)***-**-**"
