import uuid
from unittest.mock import patch

import pytest
from django.conf import settings

from apps.common.events.contracts import ContractClosedEvent
from apps.common.models.outbox import OutboxRecord
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.tasks.outbox_dispatcher import run_outbox_polling_dispatcher_task


@pytest.mark.django_db(transaction=True)
class TestOutboxReliabilityMesh:
    def test_outbox_dispatcher_retains_records_in_db_on_broker_failure(self):
        """[LEVEL 5: WEBSOCKET/RELIABILITY] Проверяет защиту от потери событий при падении Redis."""
        corr_id = uuid.uuid4()

        # 1. Генерируем событие закрытия договора и пишем в Outbox БД
        event = ContractClosedEvent.create(
            correlation_id=corr_id,
            contract_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            remaining_amount=0,
        )
        TransactionalOutboxService.push_to_outbox(event)

        assert OutboxRecord.objects.filter(is_processed=False).count() == 1

        # 2. Имитируем жесткое падение сетевого соединения с Redis брокером (сбой сети)
        with patch.object(settings.REDIS_CLIENT, "xadd", side_effect=ConnectionError("Redis connection lost")):
            with pytest.raises(ConnectionError):
                run_outbox_polling_dispatcher_task()
            # Диспетчер пытается обработать пачку и ловит эксепшен
            with pytest.raises(ConnectionError):
                run_outbox_polling_dispatcher_task()

        # [ГЛАВНАЯ ПРОВЕРКА]: Запись не перевелась в статус True, она сохранена в СУБД и уйдет на ретрай!
        assert OutboxRecord.objects.filter(is_processed=False).count() == 1
        assert OutboxRecord.objects.filter(is_processed=True).count() == 0
