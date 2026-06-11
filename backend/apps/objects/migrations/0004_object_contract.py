import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("objects", "0003_object_stage"),
    ]

    operations = [
        migrations.CreateModel(
            name="ObjectContract",
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
                ("contract_id", models.UUIDField(db_index=True)),
                (
                    "object",
                    models.ForeignKey(
                        db_index=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="object_contracts",
                        to="objects.supplyobject",
                    ),
                ),
            ],
            options={
                "db_table": "objects_object_contracts",
            },
        ),
        migrations.AddConstraint(
            model_name="objectcontract",
            constraint=models.UniqueConstraint(
                condition=models.Q(is_deleted=False),
                fields=["object_id", "contract_id"],
                name="unique_object_contract",
            ),
        ),
        migrations.AddIndex(
            model_name="objectcontract",
            index=models.Index(
                fields=["contract_id", "tenant_id"],
                name="idx_objcontract_cid_tid",
            ),
        ),
    ]

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        schema_editor.delete_model(from_state.apps.get_model(app_label, "ObjectContract"))
