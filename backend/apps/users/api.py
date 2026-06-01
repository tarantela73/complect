from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.http import HttpResponse as DjangoResponse
from ninja import Router, Schema

from apps.common.api.auth import jwt_auth
from apps.users.schemas.auth import (
    LoginRequest,
    MFASetupResponse,
    MFAStatusResponse,
    MFAVerifyRequest,
    RefreshRequest,
    TokenResponse,
)
from apps.users.services import auth_service, mfa_service

router = Router(tags=["auth"])

REFRESH_COOKIE = "refresh_token"
REFRESH_COOKIE_MAX_AGE = 7 * 24 * 3600


def _redis():
    return settings.REDIS_CLIENT


class ErrorOut(Schema):
    code: str
    message: str = ""
    detail: object = None


@router.post("/login", response={200: TokenResponse, 401: dict}, auth=None)
def login(request: HttpRequest, body: LoginRequest, response: DjangoResponse):
    try:
        access, refresh, mfa_required = auth_service.login(
            redis_client=_redis(),
            username=body.username,
            password=body.password,
            device_fingerprint=body.device_fingerprint,
        )
    except PermissionDenied as exc:
        return 401, {"error": {"code": str(exc), "message": "Неверные учётные данные.", "detail": None}}

    if refresh:
        response.set_cookie(
            REFRESH_COOKIE,
            refresh,
            max_age=REFRESH_COOKIE_MAX_AGE,
            httponly=True,
            secure=not settings.DEBUG,
            samesite="Strict",
            path="/api/v1/auth/",
        )
    return 200, TokenResponse(access_token=access, mfa_required=mfa_required)


@router.post("/refresh", response={200: TokenResponse, 401: dict}, auth=None)
def refresh(request: HttpRequest, response: DjangoResponse, body: RefreshRequest = None):
    token = request.COOKIES.get(REFRESH_COOKIE) or (body.refresh_token if body else None)
    if not token:
        return 401, {"error": {"code": "REFRESH_TOKEN_MISSING", "message": "Refresh token не предоставлен."}}

    try:
        access, new_refresh = auth_service.refresh_access_token(_redis(), token)
    except PermissionDenied as exc:
        return 401, {"error": {"code": str(exc), "message": "Refresh token недействителен или истёк."}}

    response.set_cookie(
        REFRESH_COOKIE,
        new_refresh,
        max_age=REFRESH_COOKIE_MAX_AGE,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Strict",
        path="/api/v1/auth/",
    )
    return 200, TokenResponse(access_token=access)


@router.post("/logout", response={204: None}, auth=None)
def logout(request: HttpRequest, response: DjangoResponse):
    token = request.COOKIES.get(REFRESH_COOKIE)
    if token:
        auth_service.logout(_redis(), token)
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth/")
    return 204, None


@router.post("/mfa/verify", response={200: TokenResponse, 401: dict}, auth=None)
def mfa_verify(request: HttpRequest, body: MFAVerifyRequest, response: DjangoResponse):
    partial_token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not partial_token:
        return 401, {"error": {"code": "MFA_TOKEN_MISSING", "message": "Partial MFA token не предоставлен."}}

    try:
        access, refresh = mfa_service.verify_mfa_and_issue_tokens(_redis(), partial_token, body.totp_code)
    except PermissionDenied as exc:
        return 401, {"error": {"code": str(exc), "message": "MFA верификация не пройдена."}}

    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=REFRESH_COOKIE_MAX_AGE,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Strict",
        path="/api/v1/auth/",
    )
    return 200, TokenResponse(access_token=access)


@router.post("/mfa/setup", response={200: MFASetupResponse, 403: dict}, auth=jwt_auth)
def mfa_setup(request: HttpRequest):
    from apps.users.models import User

    try:
        user = User.objects.get(pk=request.jwt_user_id)
    except User.DoesNotExist:
        return 403, {"error": {"code": "USER_NOT_FOUND"}}

    secret, otpauth_url = mfa_service.setup_totp(user)
    return 200, MFASetupResponse(secret=secret, otpauth_url=otpauth_url)


@router.post("/mfa/confirm", response={200: MFAStatusResponse, 400: dict}, auth=jwt_auth)
def mfa_confirm(request: HttpRequest, body: MFAVerifyRequest):
    from apps.users.models import User

    try:
        user = User.objects.get(pk=request.jwt_user_id)
    except User.DoesNotExist:
        return 400, {"error": {"code": "USER_NOT_FOUND"}}

    try:
        mfa_service.confirm_totp_setup(user, body.totp_code)
    except PermissionDenied as exc:
        return 400, {"error": {"code": str(exc), "message": "Неверный код MFA."}}

    return 200, MFAStatusResponse(mfa_enabled=True)
