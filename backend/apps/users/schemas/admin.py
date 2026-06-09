from typing import Optional

from ninja import Schema


class UserListItemSchema(Schema):
    id: int
    email: str
    full_name: str
    is_active: bool
    roles: list[str]
    last_login: Optional[str] = None
    created_at: str


class UserListResponse(Schema):
    items: list[UserListItemSchema]
    total: int


class CreateUserRequest(Schema):
    email: str
    full_name: str
    roles: list[str]
    send_invite_email: bool = False


class CreateUserResponse(Schema):
    id: int
    email: str
    full_name: str
    roles: list[str]
    temp_password: str


class AssignRolesRequest(Schema):
    roles: list[str]


class RoleDetailSchema(Schema):
    name: str
    description: str
    permissions: list[str]


class RoleListResponse(Schema):
    items: list[RoleDetailSchema]
