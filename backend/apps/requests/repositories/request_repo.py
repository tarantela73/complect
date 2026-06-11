import uuid

from django.core.exceptions import PermissionDenied

from apps.common.tenancy.context_core import get_tenant_id
from apps.requests.models.request import Request


class RequestRepository:
    @staticmethod
    def get_queryset():
        return Request.objects.filter(tenant_id=get_tenant_id(), is_deleted=False)

    @classmethod
    def get_by_id(cls, request_id: uuid.UUID) -> Request:
        req = cls.get_queryset().filter(id=request_id).first()
        if not req:
            raise PermissionDenied("Заявка не найдена или недоступна для текущего тенанта.")
        return req

    @staticmethod
    def create(request: Request) -> Request:
        request.tenant_id = get_tenant_id()
        request.save()
        return request
