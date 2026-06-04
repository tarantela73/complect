import uuid

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.common.cache.dashboard_cache import invalidate_dashboard
from apps.common.events.contracts import ContractActivatedEvent
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.tenancy.context_core import get_context
from apps.contracts.models.contract import ContractStatus
from apps.contracts.repositories.contract_repo import ContractRepository


class ContractActivateService:
    @staticmethod
    def execute(contract_id: uuid.UUID) -> None:
        ctx = get_context()

        with transaction.atomic():
            contract = ContractRepository.get_by_id_for_update(contract_id)

            if contract.is_legal_hold:
                raise ValidationError("Договор заблокирован юридическим удержанием.")

            if contract.status != ContractStatus.DRAFT:
                raise ValidationError(
                    f"Активировать можно только договор в статусе DRAFT. Текущий статус: {contract.status}."
                )

            success = ContractRepository.atomic_compare_and_swap(contract, status=ContractStatus.ACTIVE)
            if not success:
                raise ValidationError("Коллизия одновременной модификации (OCC). Повторите запрос.")

            event = ContractActivatedEvent.create(
                correlation_id=ctx.correlation_id,
                contract_id=contract_id,
                user_id=ctx.user_id,
            )
            TransactionalOutboxService.push_to_outbox(event)
            _tid = ctx.tenant_id
            transaction.on_commit(lambda t=_tid: invalidate_dashboard(t))
