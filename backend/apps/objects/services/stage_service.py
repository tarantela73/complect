import uuid
from typing import Optional

from django.core.exceptions import ValidationError

from apps.objects.models.object_stage import ObjectStageStatus
from apps.objects.repositories.stage_repo import StageRepository


class StageUpdateService:
    @staticmethod
    def update(
        object_id: uuid.UUID,
        stage_key: str,
        status: str,
        note: Optional[str] = None,
        completed_by_id: Optional[uuid.UUID] = None,
    ) -> None:
        stage = StageRepository.get_by_object_and_key(object_id, stage_key)

        if status == ObjectStageStatus.SKIPPED and not stage.is_optional:
            raise ValidationError(f"Этап '{stage_key}' обязателен и не может быть пропущен.")

        updated = StageRepository.update_stage(stage, status, note, completed_by_id)
        if not updated:
            raise ValidationError("Не удалось обновить этап.")
