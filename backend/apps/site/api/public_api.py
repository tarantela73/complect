import httpx
from django.http import HttpRequest
from ninja import Router

from apps.common.api.errors import make_error_response
from apps.requests.queries.request_queries import get_request_detail
from apps.requests.services.request_create_service import RequestCreateService
from apps.site.queries.public_queries import get_page_content
from apps.site.schemas.public_dto import (
    PublicLeadFormRequest,
    PublicLeadFormResponse,
    PublicPageContentResponse,
)
from apps.site.services.turnstile_service import verify_turnstile
from apps.users.contracts.user_info_impl import DjangoUserInfoProvider

router = Router(tags=["public"])

_user_info = DjangoUserInfoProvider()


@router.get("/cms/{slug}/", response={200: PublicPageContentResponse})
def get_cms_page(request: HttpRequest, slug: str):
    return 200, get_page_content(slug)


@router.post(
    "/requests/",
    response={201: PublicLeadFormResponse, 422: dict, 503: dict},
)
def submit_lead_form(request: HttpRequest, body: PublicLeadFormRequest):
    remote_ip = request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get("REMOTE_ADDR", "")

    try:
        ok = verify_turnstile(body.cf_turnstile_token, remote_ip)
    except httpx.TimeoutException:
        return 503, make_error_response(
            "EXTERNAL_SERVICE_UNAVAILABLE",
            "Сервис проверки антибот-токена недоступен. Попробуйте позже.",
        )

    if not ok:
        return 422, make_error_response(
            "CAPTCHA_FAILED",
            "Проверка антибот-токена не прошла. Обновите страницу и попробуйте снова.",
        )

    req = RequestCreateService.execute(
        client_name=body.client_name,
        client_phone=body.client_phone,
        client_email=body.client_email,
        address=body.address,
        resource_type=body.resource_type,
        comment=body.comment,
        source="WEBSITE",
    )
    return 201, PublicLeadFormResponse(id=req.id, status=req.status)
