import uuid

from django.core.exceptions import ValidationError
from django.http import HttpRequest
from ninja import Router

from apps.common.api.auth import jwt_auth
from apps.common.api.errors import make_error_response
from apps.common.api.permissions import require_permission
from apps.users.queries.user_queries import list_roles, list_users
from apps.users.schemas.admin import (
    AssignRolesRequest,
    CreateUserRequest,
    CreateUserResponse,
    RoleListResponse,
    UserListResponse,
)
from apps.users.services import user_admin_service

router = Router(tags=["admin"], auth=jwt_auth)


def _parse_validation_error(exc: ValidationError) -> tuple[str, str]:
    if hasattr(exc, "error_dict"):
        for field_errors in exc.error_dict.values():
            for error in field_errors:
                code = error.code or str(error.message)
                return code, str(error.message)
        return "VALIDATION_ERROR", str(exc)
    code = getattr(exc, "code", None) or "VALIDATION_ERROR"
    message = getattr(exc, "message", str(exc))
    return code, str(message)


@router.get("/users/", response={200: UserListResponse, 403: dict})
@require_permission("read", "users")
def list_users_endpoint(request: HttpRequest):
    tenant_id: uuid.UUID = request.jwt_tenant_id
    items = list_users(tenant_id)
    return 200, UserListResponse(
        items=[
            {
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "is_active": u.is_active,
                "roles": u.roles,
                "last_login": u.last_login,
                "created_at": u.created_at,
            }
            for u in items
        ],
        total=len(items),
    )


@router.post("/users/", response={201: CreateUserResponse, 403: dict, 422: dict})
@require_permission("create", "users")
def create_user_endpoint(request: HttpRequest, body: CreateUserRequest):
    tenant_id: uuid.UUID = request.jwt_tenant_id
    actor_id: uuid.UUID = request.jwt_user_id

    try:
        user, temp_pwd = user_admin_service.create_user(
            tenant_id=tenant_id,
            email=body.email,
            full_name=body.full_name,
            roles=body.roles,
            actor_id=actor_id,
        )
    except ValidationError as exc:
        code, msg = _parse_validation_error(exc)
        return 422, make_error_response(code, msg)

    return 201, CreateUserResponse(
        id=user.pk,
        email=user.email,
        full_name=user.first_name,
        roles=body.roles,
        temp_password=temp_pwd,
    )


@router.post("/users/{user_id}/disable/", response={204: None, 403: dict, 422: dict})
@require_permission("write", "users")
def disable_user_endpoint(request: HttpRequest, user_id: int):
    tenant_id: uuid.UUID = request.jwt_tenant_id
    actor_id: uuid.UUID = request.jwt_user_id

    try:
        user_admin_service.disable_user(
            tenant_id=tenant_id, user_id=user_id, actor_id=actor_id
        )
    except ValidationError as exc:
        code, msg = _parse_validation_error(exc)
        return 422, make_error_response(code, msg)

    return 204, None


@router.post("/users/{user_id}/enable/", response={204: None, 403: dict})
@require_permission("write", "users")
def enable_user_endpoint(request: HttpRequest, user_id: int):
    tenant_id: uuid.UUID = request.jwt_tenant_id
    actor_id: uuid.UUID = request.jwt_user_id

    user_admin_service.enable_user(
        tenant_id=tenant_id, user_id=user_id, actor_id=actor_id
    )
    return 204, None


@router.post("/users/{user_id}/roles/", response={200: dict, 403: dict, 422: dict})
@require_permission("write", "users")
def assign_roles_endpoint(request: HttpRequest, user_id: int, body: AssignRolesRequest):
    tenant_id: uuid.UUID = request.jwt_tenant_id
    actor_id: uuid.UUID = request.jwt_user_id

    try:
        user_admin_service.assign_roles(
            tenant_id=tenant_id,
            user_id=user_id,
            roles=body.roles,
            actor_id=actor_id,
        )
    except ValidationError as exc:
        code, msg = _parse_validation_error(exc)
        return 422, make_error_response(code, msg)

    return 200, {"ok": True}


@router.get("/roles/", response={200: RoleListResponse, 403: dict})
@require_permission("read", "users")
def list_roles_endpoint(request: HttpRequest):
    items = list_roles()
    return 200, RoleListResponse(
        items=[
            {
                "name": r.name,
                "description": r.description,
                "permissions": r.permissions,
            }
            for r in items
        ]
    )
