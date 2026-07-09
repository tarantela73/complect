import pytest
from django.core.exceptions import ValidationError

from apps.integration.anti_corruption.ones_acl import OnesAntiCorruptionLayerFacade
from apps.integration.models.inbox import IntegrationInboxRecord


@pytest.mark.django_db(transaction=True)
class TestAntiCorruptionLayerMesh:
    def test_acl_inbox_blocks_duplicate_1c_packages_and_protects_domain(self):
        """[LEVEL 11: INTEGRATIONS] Гарантирует атомарную дедупликацию и изоляцию доменного ядра через ACL."""

        # Сырой JSON пакет, имитирующий выгрузку платежки из 1С Предприятия
        raw_1c_packet = {
            "DocGUID": "1c-finance-uuid-999",
            "OrganizationINN": "7701020304",
            "SummaDoc": 1500.50,  # 1500 рублей 50 копеек
            "NaznacheniePlat": "Оплата по договору снабжения воды N-200",
        }

        # 1. Проводим пакет первый раз через ACL Фасад
        OnesAntiCorruptionLayerFacade.process_incoming_1c_payment(raw_1c_packet)

        # Проверяем, что в Inbox появилась отметка о фиксации
        assert (
            IntegrationInboxRecord.objects.filter(external_event_id="1c-finance-uuid-999", status="COMPLETED").count()
            == 1
        )

        # 2. Имитируем сетевой сбой: 1С повторно присылает ТОТ ЖЕ САМЫЙ пакет (Retry Storm)
        # ACL обязан выбросить ошибку валидации на уровне Inbox констрейнта, защитив балансы леджера
        with pytest.raises(ValidationError, match="уже был успешно обработан"):
            OnesAntiCorruptionLayerFacade.process_incoming_1c_payment(raw_1c_packet)

        # Количество завершенных проводок в системе по-прежнему строго равно единице!
        assert IntegrationInboxRecord.objects.filter(external_event_id="1c-finance-uuid-999").count() == 1
