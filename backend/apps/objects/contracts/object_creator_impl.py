import uuid

from apps.common.contracts.objects_contracts import ObjectCreationDTO
from apps.objects.schemas.object_dto import SupplyObjectCreateRequest
from apps.objects.services.object_service import ObjectCreateService


class DjangoObjectCreator:
    """[ADR-0005] ObjectCreatorProtocol impl — delegates to ObjectCreateService."""

    def create_supply_object(self, data: ObjectCreationDTO) -> uuid.UUID:
        req = SupplyObjectCreateRequest(
            name=data.name,
            address=data.address,
            object_type=data.object_type,
            resource_type=data.resource_type,
            contragent_id=data.contragent_id,
        )
        obj = ObjectCreateService.execute(req)
        return obj.id
