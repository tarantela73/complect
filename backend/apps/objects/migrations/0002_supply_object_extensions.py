from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("objects", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplyobject",
            name="status",
            field=models.CharField(
                choices=[
                    ("NEW", "Новый"),
                    ("IN_PROGRESS", "В работе"),
                    ("PAUSED", "Приостановлен"),
                    ("COMPLETED", "Завершён"),
                    ("CANCELLED", "Отменён"),
                ],
                default="NEW",
                db_index=True,
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="supplyobject",
            name="description",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="supplyobject",
            name="manager_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="supplyobject",
            name="foreman_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="supplyobject",
            name="team_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="supplyobject",
            name="planned_start_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="supplyobject",
            name="planned_end_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="supplyobject",
            name="actual_start_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="supplyobject",
            name="actual_end_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
