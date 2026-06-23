import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Task",
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
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, editable=False, null=True),
                ),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                ("version", models.PositiveIntegerField(default=1, editable=False)),
                ("is_legal_hold", models.BooleanField(db_index=True, default=False)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "Открыта"),
                            ("in_progress", "В работе"),
                            ("done", "Выполнена"),
                            ("cancelled", "Отменена"),
                        ],
                        db_index=True,
                        default="open",
                        max_length=20,
                    ),
                ),
                (
                    "priority",
                    models.CharField(
                        choices=[
                            ("low", "Низкий"),
                            ("normal", "Нормальный"),
                            ("high", "Высокий"),
                        ],
                        default="normal",
                        max_length=10,
                    ),
                ),
                ("created_by_id", models.UUIDField(db_index=True)),
                (
                    "assigned_to_id",
                    models.UUIDField(blank=True, db_index=True, null=True),
                ),
                ("due_date", models.DateField(blank=True, null=True)),
                ("entity_type", models.CharField(blank=True, default="", max_length=50)),
                ("entity_id", models.UUIDField(blank=True, null=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("closed_by_id", models.UUIDField(blank=True, null=True)),
            ],
            options={
                "db_table": "tasks_tasks",
                "indexes": [
                    models.Index(fields=["tenant_id", "status", "is_deleted"]),
                    models.Index(fields=["tenant_id", "assigned_to_id", "is_deleted"]),
                ],
            },
        ),
        migrations.CreateModel(
            name="TaskAttachment",
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
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, editable=False, null=True),
                ),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                ("version", models.PositiveIntegerField(default=1, editable=False)),
                ("is_legal_hold", models.BooleanField(db_index=True, default=False)),
                ("task_id", models.UUIDField(db_index=True)),
                ("file_id", models.UUIDField(db_index=True)),
                ("attached_by_id", models.UUIDField()),
            ],
            options={
                "db_table": "tasks_attachments",
                "unique_together": {("task_id", "file_id", "is_deleted")},
            },
        ),
    ]
