import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("contracts", "0010_runtime_admission_policy")]

    operations = [
        migrations.CreateModel(
            name="LegacyDocumentLink",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tenant_id", models.UUIDField(db_index=True, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True, editable=False)),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                ("version", models.PositiveIntegerField(default=1, editable=False)),
                ("is_legal_hold", models.BooleanField(db_index=True, default=False)),
                ("source_system", models.CharField(max_length=64)),
                ("actor_id", models.UUIDField(editable=False)),
                ("legacy_document_id", models.UUIDField()),
                ("target_file_id", models.UUIDField(blank=True, null=True)),
                ("target_version", models.PositiveIntegerField(default=1)),
                ("legacy_revision", models.CharField(max_length=255)),
                ("source_snapshot_sha256", models.CharField(max_length=64)),
                ("payload_sha256", models.CharField(max_length=64)),
                ("checksum_sha256", models.CharField(max_length=64)),
                ("storage_namespace", models.CharField(max_length=128)),
                ("object_key", models.TextField()),
                ("size_bytes", models.BigIntegerField()),
                (
                    "mapping_state",
                    models.CharField(
                        choices=[("UNRESOLVED", "Не разрешён"), ("RESOLVED", "Разрешён"), ("CONFLICT", "Конфликт")],
                        db_index=True,
                        default="UNRESOLVED",
                        max_length=16,
                    ),
                ),
            ],
            options={"db_table": "contracts_legacy_document_links"},
        ),
        migrations.AddConstraint(
            model_name="legacydocumentlink",
            constraint=models.UniqueConstraint(
                fields=("source_system", "legacy_document_id"), name="contracts_legacy_link_source_doc_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="legacydocumentlink",
            constraint=models.CheckConstraint(
                condition=models.Q(source_system="COMMON_DOCUMENT"), name="contracts_legacy_link_source_system"
            ),
        ),
        migrations.AddConstraint(
            model_name="legacydocumentlink",
            constraint=models.CheckConstraint(
                condition=models.Q(source_snapshot_sha256__regex=r"^[0-9a-f]{64}$")
                & models.Q(payload_sha256__regex=r"^[0-9a-f]{64}$")
                & models.Q(checksum_sha256__regex=r"^[0-9a-f]{64}$"),
                name="contracts_legacy_link_sha256_lower_hex",
            ),
        ),
        migrations.AddConstraint(
            model_name="legacydocumentlink",
            constraint=models.CheckConstraint(
                condition=models.Q(size_bytes__gte=0), name="contracts_legacy_link_size_nonnegative"
            ),
        ),
        migrations.AddConstraint(
            model_name="legacydocumentlink",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(mapping_state="RESOLVED")
                    | (
                        models.Q(target_file_id__isnull=False)
                        & ~models.Q(source_system="")
                        & ~models.Q(legacy_revision="")
                        & ~models.Q(source_snapshot_sha256="")
                        & ~models.Q(payload_sha256="")
                        & ~models.Q(checksum_sha256="")
                        & ~models.Q(storage_namespace="")
                        & ~models.Q(object_key="")
                    )
                ),
                name="contracts_legacy_link_resolved_complete",
            ),
        ),
        migrations.RunSQL(
            sql="""
            ALTER TABLE contracts_legacy_document_links ENABLE ROW LEVEL SECURITY;
            ALTER TABLE contracts_legacy_document_links FORCE ROW LEVEL SECURITY;
            CREATE POLICY legacy_link_tenant_isolation
              ON contracts_legacy_document_links AS RESTRICTIVE FOR ALL TO PUBLIC
              USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
              WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
            DO $$
            BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'complect_app_runtime') THEN
                EXECUTE 'CREATE POLICY legacy_link_runtime_admission ON public.contracts_legacy_document_links AS PERMISSIVE FOR ALL TO complect_app_runtime USING (true) WITH CHECK (true)';
              END IF;
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'complect_app_local') THEN
                EXECUTE 'CREATE POLICY legacy_link_local_admission ON public.contracts_legacy_document_links AS PERMISSIVE FOR ALL TO complect_app_local USING (true) WITH CHECK (true)';
              END IF;
            END $$;
            DO $$
            BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'complect_app_runtime') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON TABLE public.contracts_legacy_document_links TO complect_app_runtime';
              END IF;
            END $$;
            """,
            reverse_sql="""
            DROP POLICY IF EXISTS legacy_link_local_admission ON contracts_legacy_document_links;
            DROP POLICY IF EXISTS legacy_link_runtime_admission ON contracts_legacy_document_links;
            DROP POLICY IF EXISTS legacy_link_tenant_isolation ON contracts_legacy_document_links;
            ALTER TABLE contracts_legacy_document_links DISABLE ROW LEVEL SECURITY;
            """,
        ),
    ]
