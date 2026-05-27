from apps.integration.schemas.ones_dto import (
    RawOnesPaymentInputSchema,
    InternalLedgerTransactionDTO,
)


class OnesDataMapper:
    """
    [ИСПРАВЛЕНО] Слой чистых детерминированных мапперов без сайд-эффектов.
    Переводит концепции внешней системы 1С во внутренние DTO платформы Комплект.
    """

    @staticmethod
    def map_external_payment_to_internal(
        raw_data: RawOnesPaymentInputSchema,
    ) -> InternalLedgerTransactionDTO:
        # 1. Избавляемся от float: рубли из 1С пересчитываем в копейки (BigInt)
        cents = int(round(raw_data.SummaDoc * 100))

        # 2. Формируем чистый внутренний DTO контракт
        return InternalLedgerTransactionDTO(
            external_ref_id=raw_data.DocGUID,
            tenant_inn=raw_data.OrganizationINN,
            amount_in_cents=cents,
            description=raw_data.NaznacheniePlat,
        )
