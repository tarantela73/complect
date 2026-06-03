from django.db import migrations, models


class Migration(migrations.Migration):
    # atomic=False required: CONCURRENTLY index cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("common", "0009_add_processed_event_causal_root_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="outboxrecord",
            name="aggregate_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="outboxrecord",
            name="aggregate_type",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="outboxrecord",
            name="aggregate_seq",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS outbox_aggregate_seq_idx
                ON common_transactional_outbox (aggregate_type, aggregate_id, aggregate_seq)
                WHERE aggregate_seq IS NOT NULL;
            """,
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS outbox_aggregate_seq_idx;",
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="outboxrecord",
                    index=models.Index(
                        condition=models.Q(aggregate_seq__isnull=False),
                        fields=["aggregate_type", "aggregate_id", "aggregate_seq"],
                        name="outbox_aggregate_seq_idx",
                    ),
                ),
            ],
        ),
    ]
