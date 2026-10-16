import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("common", "0027_seed_director_role")]

    operations = [
        migrations.CreateModel(
            name="LegacyTaskRecovery",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("task_id", models.CharField(max_length=255, unique=True)),
                ("doc_id", models.CharField(max_length=255)),
                ("task_name", models.CharField(max_length=255)),
                ("args_payload", models.JSONField(default=list)),
                ("kwargs_payload", models.JSONField(default=dict)),
                ("metadata_payload", models.JSONField(default=dict)),
                ("retries", models.PositiveIntegerField(default=0)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("UNRESOLVED", "Unresolved"),
                            ("READY_TO_REPLAY", "Ready to replay"),
                            ("PROCESSING", "Processing"),
                            ("FAILED", "Failed"),
                            ("COMPLETED", "Completed"),
                        ],
                        db_index=True,
                        default="UNRESOLVED",
                        max_length=20,
                    ),
                ),
                ("tenant_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("assigned_by_actor_id", models.UUIDField(blank=True, null=True)),
                ("assigned_at", models.DateTimeField(blank=True, null=True)),
                ("failure_reason", models.TextField(blank=True)),
                ("capability_sha256", models.CharField(blank=True, max_length=64)),
                ("scan_proof", models.JSONField(default=dict)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "common_legacy_task_recoveries",
                "indexes": [
                    models.Index(fields=["status", "created_at"], name="legacy_recovery_status_idx"),
                    models.Index(fields=["tenant_id", "status"], name="legacy_recovery_tenant_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="legacytaskrecovery",
            constraint=models.CheckConstraint(
                condition=models.Q(retries__gte=0), name="legacy_recovery_retries_nonnegative"
            ),
        ),
        migrations.AddConstraint(
            model_name="legacytaskrecovery",
            constraint=models.CheckConstraint(
                condition=models.Q(version__gte=1), name="legacy_recovery_version_positive"
            ),
        ),
        migrations.RunSQL(
            sql="""
            ALTER TABLE common_legacy_task_recoveries ENABLE ROW LEVEL SECURITY;
            ALTER TABLE common_legacy_task_recoveries FORCE ROW LEVEL SECURITY;
            CREATE POLICY legacy_recovery_global_or_tenant ON common_legacy_task_recoveries
            USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'complect_app_runtime') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON TABLE public.common_legacy_task_recoveries TO complect_app_runtime';
              END IF;
            END $$;
            """,
            reverse_sql="""
            DROP POLICY IF EXISTS legacy_recovery_global_or_tenant ON common_legacy_task_recoveries;
            ALTER TABLE common_legacy_task_recoveries NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE common_legacy_task_recoveries DISABLE ROW LEVEL SECURITY;
            """,
        ),
    ]
