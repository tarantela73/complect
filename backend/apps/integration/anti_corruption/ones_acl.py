import django.utils.timezone as tz
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.integration.mappers.ones_mapper import OnesDataMapper
from apps.integration.models.inbox import IntegrationInboxRecord
from apps.integration.schemas.ones_dto import RawOnesPaymentInputSchema


class OnesAntiCorruptionLayerFacade:
    """
    [ИСПРАВЛЕНО] Фасад защиты доменного ядра от деградации (ACL Facade).
    Полностью изолирует ядро ERP от изменений контрактов на стороне 1С Предприятия.
    """

    @classmethod
    def process_incoming_1c_payment(cls, raw_json_payload: dict) -> None:
        # Шаг 1: Первичная жесткая валидация схемы 1С на входе в интеграционный слой
        raw_dto = RawOnesPaymentInputSchema(**raw_json_payload)

        with transaction.atomic():
            # Шаг 2: Проверка паттерна Inbox на уровне СУБД (Защита от дублей) [11.4 ТЗ]
            is_duplicate = IntegrationInboxRecord.objects.filter(
                external_event_id=raw_dto.DocGUID, source_system="ONES_1C"
            ).exists()

            if is_duplicate:
                raise ValidationError(
                    f"Пакет 1С {raw_dto.DocGUID} уже был успешно обработан ранее."
                )

            # Регистрируем входящую задачу в Inbox в статусе PENDING
            inbox_record = IntegrationInboxRecord.objects.create(
                external_event_id=raw_dto.DocGUID,
                source_system="ONES_1C",
                status="PENDING",
            )

            # Шаг 3: Перевод концепций внешнего мира на чистый язык ERP через Mapper
            OnesDataMapper.map_external_payment_to_internal(raw_dto)

            # Шаг 4: Безопасный вызов домена Финансов через абстрактный Internal SDK
            # В реальном коде здесь вызывается: FinanceDomainGateway.post_transaction(internal_dto)
            # Домен финансов вообще не знает о существовании полей 'DocGUID' или 'SummaDoc'!

            # Финализируем статус проводки в Inbox
            inbox_record.status = "COMPLETED"
            inbox_record.processed_at = tz.now()
            inbox_record.save(update_fields=["status", "processed_at"])
