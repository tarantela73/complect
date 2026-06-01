import uuid

import django.utils.timezone as tz
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db.models import F

from apps.common.tenancy.context import get_current_tenant_id


class ConcurrentModificationError(Exception):
    """Исключение при фиксации коллизии OCC."""

    pass


class EnterpriseSreRepository:
    """
    Единственный легитимный инструмент работы с БД (Command Side).
    Изолирует выборки, принудительно внедряет tenant_id, реализует атомарный CAS и OCC.
    """

    @staticmethod
    def get_by_id(model_class, pk: uuid.UUID):
        """Гарантированная изоляция чтения под защитой текущего тенанта."""
        tenant_id = get_current_tenant_id()
        try:
            return model_class.objects.filter(
                pk=pk, tenant_id=tenant_id, is_deleted=False
            ).get()
        except ObjectDoesNotExist:
            raise PermissionDenied("Доступ запрещен или объект не найден.") from None

    @staticmethod
    def atomic_cas_update(instance, **update_fields) -> None:
        """Чистый, атомарный Compare-And-Swap."""
        tenant_id = get_current_tenant_id()
        current_v = instance.version

        update_fields["version"] = F("version") + 1
        update_fields["updated_at"] = tz.now()

        updated = (
            type(instance)
            .objects.filter(
                pk=instance.pk,
                tenant_id=tenant_id,
                version=current_v,
                is_legal_hold=False,
            )
            .update(**update_fields)
        )

        if updated == 0:
            raise ConcurrentModificationError(
                "OCC Conflict: Запись была изменена параллельным процессом."
            )

    @staticmethod
    def enforce_soft_delete(instance) -> None:
        """Безопасное мягкое удаление, полностью вынесенное из модели."""
        tenant_id = get_current_tenant_id()
        if instance.is_legal_hold:
            raise ValidationError(
                "Юридическое удержание активировано. Удаление заблокировано."
            )

        updated = (
            type(instance)
            .objects.filter(pk=instance.pk, tenant_id=tenant_id, is_legal_hold=False)
            .update(is_deleted=True, deleted_at=tz.now(), version=F("version") + 1)
        )

        if updated == 0:
            raise ConcurrentModificationError("Не удалось выполнить мягкое удаление.")
