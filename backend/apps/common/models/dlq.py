from django.db import models


class DeadLetterRecord(models.Model):
    """Вместо волатильного Redis-списка сохраняем криминалистические улики в СУБД [Пункт 8]"""

    id = models.UUIDField(primary_key=True, editable=False)
    task_name = models.CharField(max_length=255, db_index=True)
    args_payload = models.JSONBField()
    kwargs_payload = models.JSONBField()
    exception_class = models.CharField(max_length=255)
    exception_message = models.TextField()
    traceback_text = models.TextField()
    failed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    correlation_id = models.UUIDField(db_index=True)

    class Meta:
        db_table = "infrastructure_dead_letter_records"
