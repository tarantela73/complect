from django.db import connection, migrations


def _reverse_index(apps, schema_editor):
    # schema_editor.execute() triggers ActiveSqlTransaction for CONCURRENTLY DDL
    # in the backward path; bypass via direct connection cursor.
    with connection.cursor() as cursor:
        cursor.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS tasks_attachments_active_unique;"
        )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE UNIQUE INDEX CONCURRENTLY tasks_attachments_active_unique
            ON tasks_attachments (task_id, file_id)
            WHERE is_deleted = FALSE;
            """
        )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("tasks", "0003_taskcomment"),
    ]

    operations = [
        migrations.RunSQL(
            sql="DROP INDEX CONCURRENTLY IF EXISTS tasks_attachments_active_unique;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="""
                CREATE UNIQUE INDEX CONCURRENTLY tasks_attachments_active_unique
                ON tasks_attachments (task_id, file_id, tenant_id)
                WHERE is_deleted = FALSE;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunPython(
            migrations.RunPython.noop,
            _reverse_index,
        ),
    ]
