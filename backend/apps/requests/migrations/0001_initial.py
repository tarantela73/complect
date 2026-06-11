import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Request",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("tenant_id", models.UUIDField(db_index=True, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                ("version", models.PositiveIntegerField(default=1, editable=False)),
                ("is_legal_hold", models.BooleanField(db_index=True, default=False)),
                ("client_name", models.CharField(max_length=255)),
                ("client_phone", models.CharField(max_length=20)),
                ("client_email", models.CharField(blank=True, default="", max_length=255)),
                ("address", models.CharField(max_length=500)),
                (
                    "resource_type",
                    models.CharField(
                        choices=[
                            ("ELECTRICITY", "Электроснабжение"),
                            ("GAS", "Газоснабжение"),
                            ("WATER", "Водоснабжение"),
                            ("HEAT", "Теплоснабжение"),
                            ("OTHER", "Прочее"),
                        ],
                        db_index=True,
                        default="OTHER",
                        max_length=30,
                    ),
                ),
                ("comment", models.TextField(blank=True, default="")),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("WEBSITE", "Сайт"),
                            ("MANUAL", "Ручной ввод"),
                            ("PHONE", "Телефон"),
                        ],
                        default="MANUAL",
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("NEW", "Новая"),
                            ("ASSIGNED", "Назначена"),
                            ("PROCESSING", "В обработке"),
                            ("CONVERTED", "Конвертирована"),
                            ("CLOSED", "Закрыта"),
                        ],
                        db_index=True,
                        default="NEW",
                        max_length=20,
                    ),
                ),
                ("manager_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("converted_object_id", models.UUIDField(blank=True, null=True)),
                ("converted_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "requests_requests",
                "ordering": ["-created_at"],
            },
        ),
    ]

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        schema_editor.delete_model(from_state.apps.get_model(app_label, "Request"))
