from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False  # CREATE INDEX CONCURRENTLY cannot run inside a transaction

    dependencies = [
        ("common", "0004_add_processed_events"),
    ]

    operations = [
        migrations.AddField(
            model_name="processedevent",
            name="acked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS processed_event_unacked_idx
                ON common_processed_events (consumer, tenant_id, processed_at)
                WHERE acked_at IS NULL
            """,
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS processed_event_unacked_idx",
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="processedevent",
                    index=models.Index(
                        fields=["consumer", "tenant_id", "processed_at"],
                        name="processed_event_unacked_idx",
                        condition=models.Q(acked_at__isnull=True),
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
