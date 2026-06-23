from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class TaskAttachment(BaseEnterpriseModel):
    # Cross-domain UUID references without ORM FK (ADR-0005)
    task_id = models.UUIDField(db_index=True)
    file_id = models.UUIDField(db_index=True)
    attached_by_id = models.UUIDField()

    class Meta:
        db_table = "tasks_attachments"
        unique_together = [["task_id", "file_id", "is_deleted"]]
