import uuid

from django.db import transaction

from apps.common.security.audit import AuditIntegrityEngine
from apps.common.tenancy.context_core import get_context
from apps.contragents.models.contragent import Contragent
from apps.contragents.policies.validation_matrix import ContragentValidationPolicy
from apps.contragents.repositories.person_repo import PersonProfileRepository


class ContragentRegistrationService:
    """[СИНХРОНИЗИРОВАНО С CONTEXT_CORE] Оркестратор мутаций профилей."""

    @staticmethod
    def register_person_profile(
        contragent_id: uuid.UUID,
        first_name: str,
        last_name: str,
        middle_name: str,
        passport: str,
        snils: str,
        phone: str,
    ) -> uuid.UUID:
        ContragentValidationPolicy.verify_fio_regex(first_name, last_name)

        # [ИСПРАВЛЕНО]: Получение единого объекта контекста
        ctx = get_context()

        with transaction.atomic():
            contragent = Contragent.objects.get(id=contragent_id)

            profile = PersonProfileRepository.create_secure_profile(
                contragent=contragent,
                first_name=first_name,
                last_name=last_name,
                middle_name=middle_name,
                passport=passport,
                snils=snils,
                phone=phone,
            )

            # Передаем структурированные данные в крипто-аудит [Пункт 13 ТЗ]
            audit_payload = {
                "action": "PII_PROFILE_CREATED",
                "person_profile_id": str(profile.id),
                "contragent_id": str(contragent_id),
                "operator_user_id": str(ctx.user_id),
                "request_id": str(ctx.request_id),
            }
            AuditIntegrityEngine.append_log(audit_payload)

            return profile.id
