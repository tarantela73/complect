import uuid

from django.core.exceptions import PermissionDenied

from apps.objects.models.object_contract import ObjectContract
from apps.objects.repositories.object_contract_repo import ObjectContractRepository
from apps.objects.repositories.object_repo import ObjectRepository


class ObjectContractService:
    @staticmethod
    def link(object_id: uuid.UUID, contract_id: uuid.UUID) -> ObjectContract:
        ObjectRepository.get_by_id(object_id)
        return ObjectContractRepository.link(object_id, contract_id)

    @staticmethod
    def unlink(object_id: uuid.UUID, contract_id: uuid.UUID) -> None:
        ObjectRepository.get_by_id(object_id)
        ObjectContractRepository.unlink(object_id, contract_id)
