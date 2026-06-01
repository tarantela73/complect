import uuid

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.common.events.contracts import ContractClosedEvent
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.contracts.models.contract import ContractStatus
from apps.contracts.policies.closure_policy import ContractClosurePolicy
from apps.contracts.repositories.contract_repo import ContractRepository


class ContractClosureService:
    """Orchestration Layer для управления жизненным циклом договоров снабжения холдинга."""

    @staticmethod
    def execute_contract_closure(contract_id: uuid.UUID, user_id: uuid.UUID, correlation_id: uuid.UUID) -> None:
        """
        [COMMAND SIDE] Переводит договор в статус CLOSED по законам Strict Enterprise Architecture.
        Гарантирует атомарность изменений, OCC-защиту и At-Least-Once Outbox доставку.
        """
        # Весь процесс упаковывается в одну реляционную транзакцию СУБД [4.1]
        with transaction.atomic():
            # 1. Чтение строки с блокировкой FOR UPDATE на уровне репозитория [4.7 isolation policy]
            contract = ContractRepository.get_by_id_for_update(contract_id)

            # Имитация проверки статуса прикрепленных файлов (в реальном коде запрос к DocumentRepository)
            # Файл должен иметь stage == DocumentStage.READY в нашем FSM [5.4]
            has_ready_documents = True

            # 2. Передача чистой сущности в Policy Layer для проверки бизнес-правил
            ContractClosurePolicy.verify_closure_eligibility(contract, has_ready_documents)

            # 3. Атомарный CAS-апдейт через репозиторий с инкрементом версии
            success = ContractRepository.atomic_compare_and_swap(contract, status=ContractStatus.CLOSED)

            if not success:
                # Если параллельная транзакция успела сдвинуть version или взвесить Legal Hold
                raise ValidationError("Коллизия одновременной модификации данных (OCC). Повторите запрос.")

            # 4. Сборка строго типизированного иммутабельного события домена [4.5 DOMAIN EVENTS CONTRACT]
            event = ContractClosedEvent.create(
                correlation_id=correlation_id,
                contract_id=contract.id,
                user_id=user_id,
                remaining_amount=contract.total_amount - contract.paid_amount,
            )

            # 5. Пуш события в append-only Outbox СУБД таблицу строго внутри транзакции
            # Наш Polling Dispatcher [Пункт 12] выгребет его асинхронно после COMMIT
            TransactionalOutboxService.push_to_outbox(event)
