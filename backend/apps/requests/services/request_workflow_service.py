import uuid

import django.utils.timezone as tz
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.common.contracts.objects_contracts import ObjectCreationDTO, ObjectCreatorProtocol
from apps.requests.models.request import Request, RequestStatus
from apps.requests.repositories.request_repo import RequestRepository

_ASSIGNABLE = {RequestStatus.NEW, RequestStatus.ASSIGNED}
_CLOSABLE = {RequestStatus.NEW, RequestStatus.ASSIGNED, RequestStatus.PROCESSING}


class RequestWorkflowService:
    @staticmethod
    def assign(request_id: uuid.UUID, manager_id: uuid.UUID) -> None:
        req = RequestRepository.get_by_id(request_id)
        if req.status not in _ASSIGNABLE:
            raise ValidationError(
                f"Назначение невозможно из статуса {req.status}."
            )
        Request.objects.filter(id=request_id).update(
            status=RequestStatus.ASSIGNED, manager_id=manager_id
        )

    @staticmethod
    def start_processing(request_id: uuid.UUID) -> None:
        req = RequestRepository.get_by_id(request_id)
        if req.status != RequestStatus.ASSIGNED:
            raise ValidationError(
                f"Перевод в обработку невозможен из статуса {req.status}."
            )
        Request.objects.filter(id=request_id).update(status=RequestStatus.PROCESSING)

    @staticmethod
    def convert(request_id: uuid.UUID, creator: ObjectCreatorProtocol) -> uuid.UUID:
        req = RequestRepository.get_by_id(request_id)
        if req.status != RequestStatus.PROCESSING:
            raise ValidationError(
                f"Конвертация невозможна из статуса {req.status}."
            )
        dto = ObjectCreationDTO(
            name=req.client_name,
            address=req.address,
            resource_type=req.resource_type,
        )
        with transaction.atomic():
            object_id = creator.create_supply_object(dto)
            Request.objects.filter(id=request_id).update(
                status=RequestStatus.CONVERTED,
                converted_object_id=object_id,
                converted_at=tz.now(),
            )
        return object_id

    @staticmethod
    def close(request_id: uuid.UUID) -> None:
        req = RequestRepository.get_by_id(request_id)
        if req.status not in _CLOSABLE:
            raise ValidationError(
                f"Закрытие невозможно из статуса {req.status}."
            )
        Request.objects.filter(id=request_id).update(status=RequestStatus.CLOSED)
