from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("tasks", "0001_initial"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="taskattachment",
            unique_together=set(),
        ),
        migrations.RunSQL(
            sql="""
                CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS tasks_attachments_active_unique
                ON tasks_attachments (task_id, file_id)
                WHERE is_deleted = FALSE;
            """,
            reverse_sql="""
                DROP INDEX CONCURRENTLY IF EXISTS tasks_attachments_active_unique;
            """,
        ),
    ]
