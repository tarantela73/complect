from django.core.exceptions import ValidationError

from apps.contracts.models.contract import Contract, ContractStatus


class ContractClosurePolicy:
    """Чистые бизнес-инварианты и правила перевода договоров в финальные стейты."""

    @staticmethod
    def verify_closure_eligibility(contract: Contract, has_ready_documents: bool) -> None:
        """
        [CQRS LAW] Проверяет, готов ли договор снабжения к закрытию.
        Выбрасывает ValidationError при малейшем нарушении условий ТЗ.
        """
        if contract.status == ContractStatus.CLOSED:
            raise ValidationError("Операция отклонена: Договор уже имеет статус 'Закрыт'.")

        # Жесткая блокировка мутаций при юридическом удержании [5.6 LEGAL HOLD POLICY]
        if contract.is_legal_hold or contract.status == ContractStatus.LEGAL_HOLD:
            raise ValidationError("Юридическое удержание активировано. Любые изменения документа заблокированы.")

        # Проверка обязательного наличия проверенных файлов [Пункт 9 ТЗ]
        if not has_ready_documents:
            raise ValidationError(
                "Закрытие невозможно: в конвейере FSM отсутствуют валидные файлы актов (MIME PDF/JPG/PNG)."
            )

        # Защита от дебиторской задолженности [Раздел 2.3 ТЗ]
        if contract.paid_amount < contract.total_amount:
            remaining = (contract.total_amount - contract.paid_amount) / 100
            raise ValidationError(
                f"Закрытие отклонено: обнаружена непогашенная дебиторская задолженность в размере {remaining} руб."
            )
