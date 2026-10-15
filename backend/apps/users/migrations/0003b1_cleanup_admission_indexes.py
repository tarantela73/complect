"""Concurrent indexes for new foundation tables; no existing-table index rebuilds."""

from contextlib import contextmanager

import django.contrib.postgres.indexes
from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError


@contextmanager
def bounded_ddl(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('lock_timeout'),current_setting('statement_timeout')")
        previous = cursor.fetchone()
        cursor.execute("SET lock_timeout='2s'; SET statement_timeout='30s'")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('lock_timeout',%s,false),set_config('statement_timeout',%s,false)", previous
            )


class BoundedSQL(migrations.RunSQL):
    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        with bounded_ddl(schema_editor.connection):
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        with bounded_ddl(schema_editor.connection):
            super().database_backwards(app_label, schema_editor, from_state, to_state)


def refuse_nonempty_reverse(apps, schema_editor):
    # RunPython.atomic=True makes this check a dedicated bounded transaction.
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout='2s'; SET LOCAL statement_timeout='30s'")
        cursor.execute("SELECT pg_advisory_xact_lock_shared(5278585310052439113::bigint)")
        cursor.execute("SELECT pg_advisory_xact_lock(6507031379205739152::bigint)")
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM public.users_identity_cleanup_admissions) "
            "OR EXISTS (SELECT 1 FROM public.users_identity_hold_requests)"
        )
        if cursor.fetchone()[0]:
            raise IrreversibleError("CLEANUP_FOUNDATION_LEDGER_NOT_EMPTY")


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("users", "0003b_cleanup_admission_expand")]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                BoundedSQL(
                    [
                        "CREATE UNIQUE INDEX CONCURRENTLY uniq_hold_request_idempotency "
                        "ON public.users_identity_hold_requests (idempotency_key)",
                        "ALTER TABLE public.users_identity_hold_requests ADD CONSTRAINT uniq_hold_request_idempotency "
                        "UNIQUE USING INDEX uniq_hold_request_idempotency",
                    ],
                    reverse_sql="ALTER TABLE public.users_identity_hold_requests DROP CONSTRAINT uniq_hold_request_idempotency",
                )
            ],
            state_operations=[
                migrations.AddConstraint(
                    model_name="identityholdrequest",
                    constraint=models.UniqueConstraint(
                        fields=["idempotency_key"], name="uniq_hold_request_idempotency"
                    ),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                BoundedSQL(
                    [
                        "CREATE INDEX CONCURRENTLY cleanup_admission_cleanup_idx ON public.users_identity_cleanup_admissions (cleanup_id)"
                    ],
                    reverse_sql=["DROP INDEX CONCURRENTLY public.cleanup_admission_cleanup_idx"],
                )
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="identitycleanupadmission",
                    index=models.Index(fields=["cleanup"], name="cleanup_admission_cleanup_idx"),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                BoundedSQL(
                    [
                        "CREATE INDEX CONCURRENTLY cleanup_admission_op_idx ON public.users_identity_cleanup_admissions (operation_id)"
                    ],
                    reverse_sql=["DROP INDEX CONCURRENTLY public.cleanup_admission_op_idx"],
                )
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="identitycleanupadmission",
                    index=models.Index(fields=["operation"], name="cleanup_admission_op_idx"),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                BoundedSQL(
                    [
                        "CREATE INDEX CONCURRENTLY cleanup_live_identity_gin ON public.users_identity_cleanup_admissions USING gin (identity_ids) WHERE NOT (state = 'TERMINAL')"
                    ],
                    reverse_sql=["DROP INDEX CONCURRENTLY public.cleanup_live_identity_gin"],
                )
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="identitycleanupadmission",
                    index=django.contrib.postgres.indexes.GinIndex(
                        condition=models.Q(("state", "TERMINAL"), _negated=True),
                        fields=["identity_ids"],
                        name="cleanup_live_identity_gin",
                    ),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                BoundedSQL(
                    [
                        "CREATE INDEX CONCURRENTLY cleanup_live_actor_gin ON public.users_identity_cleanup_admissions USING gin (actor_pks) WHERE NOT (state = 'TERMINAL')"
                    ],
                    reverse_sql=["DROP INDEX CONCURRENTLY public.cleanup_live_actor_gin"],
                )
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="identitycleanupadmission",
                    index=django.contrib.postgres.indexes.GinIndex(
                        condition=models.Q(("state", "TERMINAL"), _negated=True),
                        fields=["actor_pks"],
                        name="cleanup_live_actor_gin",
                    ),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                BoundedSQL(
                    [
                        "CREATE INDEX CONCURRENTLY cleanup_live_tenant_gin ON public.users_identity_cleanup_admissions USING gin (tenant_ids) WHERE NOT (state = 'TERMINAL')"
                    ],
                    reverse_sql=["DROP INDEX CONCURRENTLY public.cleanup_live_tenant_gin"],
                )
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="identitycleanupadmission",
                    index=django.contrib.postgres.indexes.GinIndex(
                        condition=models.Q(("state", "TERMINAL"), _negated=True),
                        fields=["tenant_ids"],
                        name="cleanup_live_tenant_gin",
                    ),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                BoundedSQL(
                    [
                        "CREATE INDEX CONCURRENTLY hold_request_identity_gin ON public.users_identity_hold_requests USING gin (identity_ids)"
                    ],
                    reverse_sql=["DROP INDEX CONCURRENTLY public.hold_request_identity_gin"],
                )
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="identityholdrequest",
                    index=django.contrib.postgres.indexes.GinIndex(
                        fields=["identity_ids"], name="hold_request_identity_gin"
                    ),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                BoundedSQL(
                    [
                        "CREATE INDEX CONCURRENTLY hold_request_actor_gin ON public.users_identity_hold_requests USING gin (actor_pks)"
                    ],
                    reverse_sql=["DROP INDEX CONCURRENTLY public.hold_request_actor_gin"],
                )
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="identityholdrequest",
                    index=django.contrib.postgres.indexes.GinIndex(fields=["actor_pks"], name="hold_request_actor_gin"),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                BoundedSQL(
                    [
                        "CREATE INDEX CONCURRENTLY hold_request_tenant_gin ON public.users_identity_hold_requests USING gin (tenant_ids)"
                    ],
                    reverse_sql=["DROP INDEX CONCURRENTLY public.hold_request_tenant_gin"],
                )
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="identityholdrequest",
                    index=django.contrib.postgres.indexes.GinIndex(
                        fields=["tenant_ids"], name="hold_request_tenant_gin"
                    ),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                BoundedSQL(
                    [
                        "CREATE UNIQUE INDEX CONCURRENTLY uniq_cleanup_live_owner ON public.users_identity_cleanup_admissions (cleanup_id) WHERE NOT (state = 'TERMINAL')"
                    ],
                    reverse_sql=["DROP INDEX CONCURRENTLY public.uniq_cleanup_live_owner"],
                )
            ],
            state_operations=[
                migrations.AddConstraint(
                    model_name="identitycleanupadmission",
                    constraint=models.UniqueConstraint(
                        condition=models.Q(("state", "TERMINAL"), _negated=True),
                        fields=("cleanup",),
                        name="uniq_cleanup_live_owner",
                    ),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                BoundedSQL(
                    [
                        "CREATE UNIQUE INDEX CONCURRENTLY uniq_cleanup_admission_subject ON public.users_identity_cleanup_admission_subjects (admission_id, kind, value)",
                        "ALTER TABLE public.users_identity_cleanup_admission_subjects ADD CONSTRAINT uniq_cleanup_admission_subject UNIQUE USING INDEX uniq_cleanup_admission_subject",
                    ],
                    reverse_sql=[
                        "ALTER TABLE public.users_identity_cleanup_admission_subjects DROP CONSTRAINT uniq_cleanup_admission_subject"
                    ],
                )
            ],
            state_operations=[
                migrations.AddConstraint(
                    model_name="identitycleanupadmissionsubject",
                    constraint=models.UniqueConstraint(
                        fields=("admission", "kind", "value"), name="uniq_cleanup_admission_subject"
                    ),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                BoundedSQL(
                    [
                        "CREATE UNIQUE INDEX CONCURRENTLY uniq_cleanup_lifecycle_version ON public.users_identity_cleanup_lifecycle (admission_id, version)",
                        "ALTER TABLE public.users_identity_cleanup_lifecycle ADD CONSTRAINT uniq_cleanup_lifecycle_version UNIQUE USING INDEX uniq_cleanup_lifecycle_version",
                    ],
                    reverse_sql=[
                        "ALTER TABLE public.users_identity_cleanup_lifecycle DROP CONSTRAINT uniq_cleanup_lifecycle_version"
                    ],
                )
            ],
            state_operations=[
                migrations.AddConstraint(
                    model_name="identitycleanuplifecycleevent",
                    constraint=models.UniqueConstraint(
                        fields=("admission", "version"), name="uniq_cleanup_lifecycle_version"
                    ),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                BoundedSQL(
                    [
                        "CREATE UNIQUE INDEX CONCURRENTLY uniq_hold_lifecycle_version ON public.users_identity_hold_lifecycle (request_id, version)",
                        "ALTER TABLE public.users_identity_hold_lifecycle ADD CONSTRAINT uniq_hold_lifecycle_version UNIQUE USING INDEX uniq_hold_lifecycle_version",
                    ],
                    reverse_sql=[
                        "ALTER TABLE public.users_identity_hold_lifecycle DROP CONSTRAINT uniq_hold_lifecycle_version"
                    ],
                )
            ],
            state_operations=[
                migrations.AddConstraint(
                    model_name="identityholdlifecycleevent",
                    constraint=models.UniqueConstraint(
                        fields=("request", "version"), name="uniq_hold_lifecycle_version"
                    ),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                BoundedSQL(
                    [
                        "CREATE UNIQUE INDEX CONCURRENTLY uniq_hold_request_subject ON public.users_identity_hold_request_subjects (request_id, kind, value)",
                        "ALTER TABLE public.users_identity_hold_request_subjects ADD CONSTRAINT uniq_hold_request_subject UNIQUE USING INDEX uniq_hold_request_subject",
                    ],
                    reverse_sql=[
                        "ALTER TABLE public.users_identity_hold_request_subjects DROP CONSTRAINT uniq_hold_request_subject"
                    ],
                )
            ],
            state_operations=[
                migrations.AddConstraint(
                    model_name="identityholdrequestsubject",
                    constraint=models.UniqueConstraint(
                        fields=("request", "kind", "value"), name="uniq_hold_request_subject"
                    ),
                )
            ],
        ),
        migrations.RunPython(migrations.RunPython.noop, refuse_nonempty_reverse, atomic=True),
    ]
