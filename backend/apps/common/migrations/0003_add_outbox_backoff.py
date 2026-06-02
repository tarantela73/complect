from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("common", "0002_add_outbox_retry_tracking"),
    ]

    operations = [
        # Expand phase: nullable column only.
        migrations.AddField(
            model_name="outboxrecord",
            name="next_retry_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        # Partial index CONCURRENTLY — covers only unprocessed, non-permanently-failed records,
        # so the index stays small as the table grows.
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS outbox_ready_dispatch_idx
                ON common_transactional_outbox (next_retry_at, created_at)
                WHERE is_processed = false AND failed_permanently_at IS NULL
            """,
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS outbox_ready_dispatch_idx",
        ),
        # Record the index in Django's migration state without re-running the SQL.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="outboxrecord",
                    index=models.Index(
                        fields=["next_retry_at", "created_at"],
                        name="outbox_ready_dispatch_idx",
                        condition=Q(is_processed=False, failed_permanently_at__isnull=True),
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
