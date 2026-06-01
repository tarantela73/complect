import uuid

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.contracts.models.contract import Contract, ContractStatus, ResourceSupplyType
from apps.contracts.repositories.contract_repo import ContractRepository
from apps.contracts.schemas.contract_dto import ContractCreateRequestSchema, ContractResponseSchema


class ContractCreateService:
    @staticmethod
    def execute(request: ContractCreateRequestSchema) -> ContractResponseSchema:
        """
        [COMMAND SIDE] Creates a new contract for the current tenant.
        Tenant is injected by ContractRepository.create() from context.
        """
        if request.resource_type not in ResourceSupplyType.values:
            raise ValidationError(
                f"Недопустимый тип ресурса: {request.resource_type}. "
                f"Допустимые значения: {', '.join(ResourceSupplyType.values)}"
            )

        with transaction.atomic():
            contract = Contract(
                id=uuid.uuid4(),
                contract_number=request.contract_number,
                title=request.title,
                resource_type=request.resource_type,
                total_amount=request.total_amount,
                paid_amount=0,
                status=ContractStatus.DRAFT,
            )
            ContractRepository.create(contract)

        return ContractResponseSchema(
            id=contract.id,
            contract_number=contract.contract_number,
            title=contract.title,
            status=contract.status,
            resource_type=contract.resource_type,
            total_amount=contract.total_amount,
            paid_amount=contract.paid_amount,
            remaining_amount=contract.total_amount - contract.paid_amount,
        )
