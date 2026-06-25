from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class TaskComment(BaseEnterpriseModel):
    # Cross-domain UUID references without ORM FK (ADR-0005)
    task_id = models.UUIDField(db_index=True)
    author_id = models.UUIDField()
    body = models.TextField(max_length=10000)

    class Meta:
        db_table = "tasks_comments"
        indexes = [
            models.Index(fields=["task_id", "created_at"], name="tasks_cmts_task_created_idx"),
        ]
