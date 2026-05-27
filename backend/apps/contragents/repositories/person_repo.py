from apps.common.security.pii_vault import PiiEncryptionVault
from apps.common.tenancy.context_core import get_tenant_id
from apps.contragents.models.contragent import Contragent, PersonProfile


class PersonProfileRepository:
    """[СИНХРОНИЗИРОВАНО С CONTEXT_CORE] Изолированное транзакционное управление ПДн граждан."""

    @staticmethod
    def create_secure_profile(
        contragent: Contragent, first_name: str, last_name: str, middle_name: str, passport: str, snils: str, phone: str
    ) -> PersonProfile:
        # [ИСПРАВЛЕНО]: Внедрение тенанта через новый токен-безопасный акцессор
        tenant_id = get_tenant_id()

        enc_passport = PiiEncryptionVault.encrypt_field(passport)
        enc_snils = PiiEncryptionVault.encrypt_field(snils)
        enc_phone = PiiEncryptionVault.encrypt_field(phone)

        profile = PersonProfile(
            tenant_id=tenant_id,
            contragent=contragent,
            first_name=first_name,
            last_name=last_name,
            middle_name=middle_name,
            encrypted_passport=enc_passport,
            encrypted_snils=enc_snils,
            encrypted_phone=enc_phone,
        )
        profile.save()
        return profile
