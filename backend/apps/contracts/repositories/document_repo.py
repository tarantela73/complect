import uuid
import django.utils.timezone as tz
from django.db.models import F
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from apps.common.tenancy.context import get_current_tenant_id
from apps.contracts.models.document import CommonDocument, DocumentStage


class DocumentConcurrencyError(Exception):
    pass


class DocumentRepository:
    """Изолированный доступ к таблице документов. Бизнес-логики внутри нет."""

    @staticmethod
    def get_by_id(doc_id: uuid.UUID) -> CommonDocument:
        tenant_id = get_current_tenant_id()
        try:
            return CommonDocument.objects.filter(
                id=doc_id, tenant_id=tenant_id, is_deleted=False
            ).get()
        except ObjectDoesNotExist:
            raise PermissionDenied("Документ не найден в контуре текущей компании.")

    @staticmethod
    def atomic_stage_transition(
        document: CommonDocument, next_stage: DocumentStage, **meta_updates
    ) -> None:
        """
        [FSM CAS] Атомарный Compare-And-Swap переход по стадиям обработки.
        Исключает конкурентное изменение статуса файла разными воркерами Celery.
        """
        tenant_id = get_current_tenant_id()
        current_version = document.version

        # Слияние метаданных (например, результатов сканирования ClamAV)
        updated_meta = {**document.meta_json, **meta_updates}

        updated_rows = CommonDocument.objects.filter(
            id=document.id,
            tenant_id=tenant_id,
            version=current_version,
            is_legal_hold=False,  # Файлы под Legal Hold защищены от изменений стейта
        ).update(
            stage=next_stage,
            meta_json=updated_meta,
            version=F("version") + 1,
            updated_at=tz.now(),
        )

        if updated_rows == 0:
            raise DocumentConcurrencyError(
                "FSM Conflict: статус файла изменен параллельной задачей."
            )
