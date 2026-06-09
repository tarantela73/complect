import uuid

from django.core.exceptions import ValidationError
from django.db import connection, transaction

from apps.common.cache.dashboard_cache import invalidate_dashboard
from apps.common.events.contracts import ContractCreatedEvent
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.tenancy.context_core import get_context
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

        ctx = get_context()

        if request.contragent_id is not None:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM contragents_contragents"
                    " WHERE id = %s AND tenant_id = %s AND is_deleted = FALSE",
                    [request.contragent_id, ctx.tenant_id],
                )
                if cursor.fetchone() is None:
                    raise ValidationError("Контрагент не найден в текущем тенанте.")

        with transaction.atomic():
            contract = Contract(
                id=uuid.uuid4(),
                contract_number=request.contract_number,
                title=request.title,
                resource_type=request.resource_type,
                total_amount=request.total_amount,
                paid_amount=0,
                status=ContractStatus.DRAFT,
                contragent_id=request.contragent_id,
            )
            ContractRepository.create(contract)

            event = ContractCreatedEvent.create(
                correlation_id=ctx.correlation_id,
                contract_id=contract.id,
                user_id=ctx.user_id,
                contract_number=contract.contract_number,
            )
            TransactionalOutboxService.push_to_outbox(event)
            _tid = ctx.tenant_id
            transaction.on_commit(lambda t=_tid: invalidate_dashboard(t))

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
