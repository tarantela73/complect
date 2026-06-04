from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("contracts", "0003_data_migration_example"),
    ]

    operations = [
        migrations.CreateModel(
            name="ContractAttachment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tenant_id", models.UUIDField(db_index=True, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                ("version", models.PositiveIntegerField(default=1, editable=False)),
                ("is_legal_hold", models.BooleanField(db_index=True, default=False)),
                ("contract", models.ForeignKey(
                    db_index=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="attachments",
                    to="contracts.contract",
                )),
                ("file_id", models.UUIDField(db_index=True)),
                ("attached_by", models.UUIDField(editable=False)),
            ],
            options={
                "db_table": "contracts_attachments",
            },
        ),
        migrations.AddIndex(
            model_name="contractattachment",
            index=models.Index(fields=["contract", "is_deleted"], name="ca_contract_deleted_idx"),
        ),
        migrations.AddIndex(
            model_name="contractattachment",
            index=models.Index(fields=["tenant_id", "file_id"], name="ca_tenant_file_idx"),
        ),
        migrations.AddConstraint(
            model_name="contractattachment",
            constraint=models.UniqueConstraint(
                condition=models.Q(is_deleted=False),
                fields=["contract", "file_id"],
                name="unique_active_contract_file",
            ),
        ),
        # Inherit base model indexes (tenant_id, created_at, id combinations)
        migrations.AddIndex(
            model_name="contractattachment",
            index=models.Index(fields=["tenant_id", "created_at"], name="ca_tenant_created_idx"),
        ),
        migrations.AddIndex(
            model_name="contractattachment",
            index=models.Index(fields=["tenant_id", "is_deleted"], name="ca_tenant_deleted_idx"),
        ),
        migrations.AddIndex(
            model_name="contractattachment",
            index=models.Index(fields=["tenant_id", "id"], name="ca_tenant_id_idx"),
        ),
        migrations.AddIndex(
            model_name="contractattachment",
            index=models.Index(fields=["tenant_id", "created_at", "id"], name="ca_tenant_cursor_idx"),
        ),
        # RLS — изоляция по тенанту как у остальных доменных таблиц
        migrations.RunSQL(
            sql="""
            ALTER TABLE contracts_attachments ENABLE ROW LEVEL SECURITY;
            ALTER TABLE contracts_attachments FORCE ROW LEVEL SECURITY;

            DROP POLICY IF EXISTS contract_attachment_tenant_policy ON contracts_attachments;
            CREATE POLICY contract_attachment_tenant_policy ON contracts_attachments
                AS RESTRICTIVE
                USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
            """,
            reverse_sql="""
            DROP POLICY IF EXISTS contract_attachment_tenant_policy ON contracts_attachments;
            ALTER TABLE contracts_attachments DISABLE ROW LEVEL SECURITY;
            """,
        ),
    ]
