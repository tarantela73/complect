import uuid

from apps.common.tenancy.context_core import get_tenant_id
from apps.objects.models.object_contract import ObjectContract
from apps.objects.models.supply_object import SupplyObject


class ObjectContractRepository:
    @staticmethod
    def get_queryset():
        return ObjectContract.objects.filter(tenant_id=get_tenant_id(), is_deleted=False)

    @classmethod
    def list_by_object(cls, object_id: uuid.UUID) -> list[ObjectContract]:
        return list(cls.get_queryset().filter(object_id=object_id))

    @classmethod
    def list_by_contract(cls, contract_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            cls.get_queryset()
            .filter(contract_id=contract_id)
            .values_list("object_id", flat=True)
        )

    @classmethod
    def link(cls, object_id: uuid.UUID, contract_id: uuid.UUID) -> ObjectContract:
        tenant_id = get_tenant_id()
        obj, _ = ObjectContract.objects.get_or_create(
            object_id=object_id,
            contract_id=contract_id,
            tenant_id=tenant_id,
            defaults={"tenant_id": tenant_id},
        )
        return obj

    @classmethod
    def unlink(cls, object_id: uuid.UUID, contract_id: uuid.UUID) -> int:
        return cls.get_queryset().filter(
            object_id=object_id, contract_id=contract_id
        ).delete()[0]
