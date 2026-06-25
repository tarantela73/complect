from django.db import migrations


def _reverse_index(apps, schema_editor):
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("DROP INDEX CONCURRENTLY IF EXISTS tasks_tasks_cursor_idx;")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("tasks", "0004_taskattachment_fix_unique_idx_tenant")]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS tasks_tasks_cursor_idx
            ON tasks_tasks (tenant_id, created_at DESC, id DESC)
            WHERE is_deleted = FALSE;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunPython(migrations.RunPython.noop, _reverse_index),
    ]
