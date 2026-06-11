import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("objects", "0002_supply_object_extensions"),
    ]

    operations = [
        migrations.CreateModel(
            name="ObjectStage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tenant_id", models.UUIDField(db_index=True, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                ("version", models.PositiveIntegerField(default=1, editable=False)),
                ("is_legal_hold", models.BooleanField(db_index=True, default=False)),
                (
                    "object",
                    models.ForeignKey(
                        db_index=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stages",
                        to="objects.supplyobject",
                    ),
                ),
                (
                    "stage_key",
                    models.CharField(
                        choices=[
                            ("REQUEST_RECEIVED", "Получена заявка"),
                            ("SITE_VISIT", "Выезд на объект"),
                            ("COST_ESTIMATION", "Расчёт стоимости"),
                            ("CONTRACT_SIGNING", "Подписание договора"),
                            ("INITIAL_PAYMENT", "Первоначальная оплата"),
                            ("INSTALLATION", "Монтаж"),
                            ("TESTING", "Испытания"),
                            ("FINAL_PAYMENT", "Финальная оплата"),
                            ("ACT_SIGNING", "Подписание акта"),
                            ("CLOSURE", "Закрытие"),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Ожидает"),
                            ("IN_PROGRESS", "В работе"),
                            ("COMPLETED", "Завершён"),
                            ("SKIPPED", "Пропущен"),
                        ],
                        db_index=True,
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                ("order", models.PositiveSmallIntegerField()),
                ("is_optional", models.BooleanField(default=False)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("completed_by_id", models.UUIDField(blank=True, null=True)),
                ("note", models.TextField(blank=True, default="")),
            ],
            options={
                "db_table": "objects_object_stages",
                "ordering": ["order"],
            },
        ),
        migrations.AddConstraint(
            model_name="objectstage",
            constraint=models.UniqueConstraint(
                fields=["object_id", "stage_key"],
                name="objects_stage_unique_object_stage_key",
            ),
        ),
    ]
