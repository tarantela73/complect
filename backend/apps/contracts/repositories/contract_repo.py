import uuid

import django.utils.timezone as tz
from django.core.exceptions import PermissionDenied
from django.db.models import F

from apps.common.tenancy.context_core import get_tenant_id
from apps.contracts.models.contract import Contract


class ContractRepository:
    @staticmethod
    def get_queryset():
        tenant_id = get_tenant_id()
        return Contract.objects.filter(tenant_id=tenant_id, is_deleted=False)

    @classmethod
    def get_by_id(cls, contract_id: uuid.UUID) -> Contract:
        contract = cls.get_queryset().filter(id=contract_id).first()
        if not contract:
            raise PermissionDenied("Договор не найден или недоступен для текущего тенанта.")
        return contract

    @classmethod
    def get_by_id_for_update(cls, contract_id: uuid.UUID) -> Contract:
        contract = cls.get_queryset().select_for_update().filter(id=contract_id).first()
        if not contract:
            raise PermissionDenied("Договор не найден или недоступен для текущего тенанта.")
        return contract

    @staticmethod
    def atomic_compare_and_swap(contract: Contract, **fields_to_update) -> bool:
        tenant_id = get_tenant_id()
        current_version = contract.version

        fields_to_update["version"] = F("version") + 1
        fields_to_update["updated_at"] = tz.now()

        updated_rows = Contract.objects.filter(
            id=contract.id,
            tenant_id=tenant_id,
            version=current_version,
            is_deleted=False,
            is_legal_hold=False,
        ).update(**fields_to_update)

        return updated_rows == 1

    @staticmethod
    def create(contract: Contract) -> Contract:
        tenant_id = get_tenant_id()
        contract.tenant_id = tenant_id
        contract.save()
        return contract
