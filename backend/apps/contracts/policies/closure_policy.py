from django.core.exceptions import ValidationError

from apps.contracts.models.contract import Contract, ContractStatus


class ContractClosurePolicyViolation(ValidationError):
    """Raised when the three-condition closure gate is not met."""

    def __init__(self, detail: dict[str, bool]) -> None:
        self.closure_detail = detail
        conditions = []
        if not detail.get("paid"):
            conditions.append("оплата не завершена")
        if not detail.get("has_signed"):
            conditions.append("отсутствует подписанный договор")
        if not detail.get("has_act"):
            conditions.append("отсутствует акт выполненных работ")
        message = "Закрытие невозможно: " + "; ".join(conditions) + "."
        super().__init__(message, code="CONTRACT_MISSING_REQUIRED_DOCUMENT")


class ContractClosurePolicy:
    """Бизнес-инварианты перевода договора в финальный статус CLOSED."""

    @staticmethod
    def verify_closure_eligibility(
        contract: Contract,
        document_conditions: dict[str, bool],
    ) -> None:
        """
        Проверяет три необходимых условия закрытия договора.
        Raises ContractClosurePolicyViolation (subclass of ValidationError) if any fails.
        """
        if contract.status == ContractStatus.CLOSED:
            raise ValidationError("Операция отклонена: Договор уже имеет статус 'Закрыт'.")

        if contract.is_legal_hold or contract.status == ContractStatus.LEGAL_HOLD:
            raise ValidationError("Юридическое удержание активировано. Любые изменения документа заблокированы.")

        paid = contract.paid_amount >= contract.total_amount
        has_signed = document_conditions.get("has_signed", False)
        has_act = document_conditions.get("has_act", False)

        if not (paid and has_signed and has_act):
            raise ContractClosurePolicyViolation(detail={"paid": paid, "has_signed": has_signed, "has_act": has_act})
