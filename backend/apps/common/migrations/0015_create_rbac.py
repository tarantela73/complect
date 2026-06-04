import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0014_create_causal_pair_repair_record"),
    ]

    operations = [
        migrations.CreateModel(
            name="Permission",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ("action", models.CharField(max_length=64)),
                ("resource", models.CharField(max_length=64)),
            ],
            options={
                "db_table": "common_rbac_permissions",
            },
        ),
        migrations.AddIndex(
            model_name="permission",
            index=models.Index(
                fields=["action", "resource"],
                name="rbac_perm_action_resource_idx",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="permission",
            unique_together={("action", "resource")},
        ),
        migrations.CreateModel(
            name="Role",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=64, unique=True)),
                ("description", models.CharField(blank=True, default="", max_length=255)),
            ],
            options={
                "db_table": "common_rbac_roles",
            },
        ),
        migrations.AddIndex(
            model_name="role",
            index=models.Index(fields=["name"], name="rbac_role_name_idx"),
        ),
        migrations.CreateModel(
            name="RolePermission",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="role_permissions",
                        to="common.role",
                    ),
                ),
                (
                    "permission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="role_permissions",
                        to="common.permission",
                    ),
                ),
            ],
            options={
                "db_table": "common_rbac_role_permissions",
            },
        ),
        migrations.AlterUniqueTogether(
            name="rolepermission",
            unique_together={("role", "permission")},
        ),
        migrations.CreateModel(
            name="RoleBinding",
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
                ("user_id", models.UUIDField(db_index=True, editable=False)),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="bindings",
                        to="common.role",
                    ),
                ),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("granted_by", models.UUIDField(blank=True, null=True)),
            ],
            options={
                "db_table": "common_rbac_role_bindings",
            },
        ),
        migrations.AddIndex(
            model_name="rolebinding",
            index=models.Index(
                fields=["tenant_id", "user_id", "is_active"],
                name="rbac_binding_lookup_idx",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="rolebinding",
            unique_together={("tenant_id", "user_id", "role")},
        ),
    ]

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        for operation in reversed(self.operations):
            operation.database_backwards(app_label, schema_editor, from_state, to_state)
