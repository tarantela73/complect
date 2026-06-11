from django.db import models

from apps.common.models.base import BaseEnterpriseModel
from apps.objects.models.supply_object import SupplyObject


class ObjectContract(BaseEnterpriseModel):
    object = models.ForeignKey(
        SupplyObject,
        on_delete=models.CASCADE,
        related_name="object_contracts",
        db_index=True,
    )
    contract_id = models.UUIDField(db_index=True)

    class Meta:
        db_table = "objects_object_contracts"
        constraints = [
            models.UniqueConstraint(
                fields=["object_id", "contract_id"],
                name="unique_object_contract",
                condition=models.Q(is_deleted=False),
            )
        ]
        indexes = [
            models.Index(fields=["contract_id", "tenant_id"], name="idx_objcontract_cid_tid"),
        ]
