import uuid
from typing import Optional

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.common.tenancy.context_core import get_tenant_id
from apps.objects.models.object_stage import ObjectStage, ObjectStageKey, ObjectStageStatus


class StageRepository:
    @staticmethod
    def get_queryset():
        return ObjectStage.objects.filter(tenant_id=get_tenant_id(), is_deleted=False)

    @classmethod
    def get_by_object_and_key(cls, object_id: uuid.UUID, stage_key: str) -> ObjectStage:
        stage = cls.get_queryset().filter(object_id=object_id, stage_key=stage_key).first()
        if not stage:
            raise PermissionDenied("Этап не найден или недоступен для текущего тенанта.")
        return stage

    @classmethod
    def list_by_object(cls, object_id: uuid.UUID) -> list[ObjectStage]:
        return list(cls.get_queryset().filter(object_id=object_id).order_by("order"))

    @staticmethod
    def update_stage(
        stage: ObjectStage,
        status: str,
        note: Optional[str],
        completed_by_id: Optional[uuid.UUID],
    ) -> int:
        fields: dict = {"status": status}
        if note is not None:
            fields["note"] = note
        if status == ObjectStageStatus.COMPLETED:
            fields["completed_at"] = timezone.now()
            if completed_by_id is not None:
                fields["completed_by_id"] = completed_by_id
        elif status in (ObjectStageStatus.PENDING, ObjectStageStatus.IN_PROGRESS, ObjectStageStatus.SKIPPED):
            fields["completed_at"] = None
            fields["completed_by_id"] = None
        return ObjectStage.objects.filter(
            id=stage.id,
            tenant_id=stage.tenant_id,
            is_deleted=False,
        ).update(**fields)
