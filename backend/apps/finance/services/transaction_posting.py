import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.common.events.finance import TransactionPostedEvent
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.tenancy.context_core import get_tenant_id
from apps.contracts.models.contract import Contract
from apps.finance.models.transaction import Transaction, TransactionStatus, TransactionType


class TransactionPostingService:
    """
    [COMMAND SIDE] Проведение финансовой транзакции по договору.

    Использует SELECT FOR UPDATE на Contract согласно правилу 4.7:
    «Finance mutations: SELECT ... FOR UPDATE (row-level lock)».
    Сумма хранится в копейках.
    """

    @staticmethod
    def post_payment(
        contract_id: uuid.UUID,
        amount: int,
        posted_by: uuid.UUID,
        external_ref: str = "",
        transaction_type: str = TransactionType.PAYMENT,
        correlation_id: uuid.UUID | None = None,
    ) -> Transaction:
        if amount <= 0:
            raise ValidationError("Сумма транзакции должна быть положительной.")

        if transaction_type not in TransactionType.values:
            raise ValidationError(f"Недопустимый тип транзакции: {transaction_type}.")

        tenant_id = get_tenant_id()

        with transaction.atomic():
            # SELECT FOR UPDATE — блокировка строки договора на время транзакции (rule 4.7)
            contract = (
                Contract.objects.select_for_update()
                .filter(id=contract_id, tenant_id=tenant_id, is_deleted=False, is_legal_hold=False)
                .first()
            )
            if not contract:
                raise PermissionDenied("Договор не найден или заблокирован юридическим удержанием.")

            # Защита от переплаты: REFUND не может превышать уже оплаченное
            if transaction_type == TransactionType.REFUND and amount > contract.paid_amount:
                raise ValidationError(
                    f"Возврат {amount / 100:.2f} руб. превышает оплаченную сумму "
                    f"{contract.paid_amount / 100:.2f} руб."
                )

            # Создаём транзакцию в статусе POSTED
            tx = Transaction(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                contract_id=contract_id,
                transaction_type=transaction_type,
                status=TransactionStatus.POSTED,
                amount=amount,
                external_ref=external_ref,
                posted_by=posted_by,
            )
            tx.save()

            # Обновляем paid_amount на договоре
            delta = amount if transaction_type == TransactionType.PAYMENT else -amount
            Contract.objects.filter(id=contract_id, tenant_id=tenant_id).update(
                paid_amount=contract.paid_amount + delta
            )

            # Emit domain event to complete the causal chain (ADR-0014: contracts → finance pair).
            TransactionalOutboxService.push_to_outbox(
                TransactionPostedEvent.create(
                    correlation_id=correlation_id or uuid.uuid4(),
                    contract_id=contract_id,
                    transaction_id=tx.id,
                    amount=amount,
                    transaction_type=transaction_type,
                )
            )

        return tx

    @staticmethod
    def reverse_transaction(tx_id: uuid.UUID, posted_by: uuid.UUID) -> Transaction:
        """Сторнирование проведённой транзакции."""
        tenant_id = get_tenant_id()

        with transaction.atomic():
            tx = (
                Transaction.objects.select_for_update()
                .filter(id=tx_id, tenant_id=tenant_id, status=TransactionStatus.POSTED)
                .first()
            )
            if not tx:
                raise PermissionDenied("Транзакция не найдена или уже сторнирована.")

            contract = (
                Contract.objects.select_for_update()
                .filter(id=tx.contract_id, tenant_id=tenant_id, is_deleted=False)
                .first()
            )
            if not contract:
                raise PermissionDenied("Договор не найден.")

            # Откатываем paid_amount
            delta = tx.amount if tx.transaction_type == TransactionType.PAYMENT else -tx.amount
            Contract.objects.filter(id=tx.contract_id, tenant_id=tenant_id).update(
                paid_amount=contract.paid_amount - delta
            )

            Transaction.objects.filter(id=tx_id, tenant_id=tenant_id).update(status=TransactionStatus.REVERSED)
            tx.status = TransactionStatus.REVERSED

        return tx
