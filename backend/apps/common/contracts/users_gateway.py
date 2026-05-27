import uuid
from typing import Protocol, runtime_checkable
from pydantic import BaseModel


class UserSessionProfileDTO(BaseModel):
    """Детерминированная плоская структура данных сессии для межмодульного транзита."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    is_active: bool
    role_name: str


@runtime_checkable
class IdentityDomainGateway(Protocol):
    """
    [ADR 0030] Абстрактный контракт взаимодействия с доменом Identity & Process.
    Выступает в роли пуленепробиваемой прослойки между доменами, исключая спагетти-импорты.
    """

    def fetch_active_session_profile(self, user_id: uuid.UUID) -> UserSessionProfileDTO:
        """Метод-заглушка контракта. Реализуется строго внутри домена apps/users/."""
        ...

    def verify_rbac_permission(self, user_id: uuid.UUID, permission_name: str) -> bool:
        """Проверяет права доступа сотрудника без прямого обращения к таблицам ролей."""
        ...
