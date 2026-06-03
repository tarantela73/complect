from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("common", "0007_add_processed_event_lease"),
    ]

    operations = [
        migrations.AddField(
            model_name="outboxrecord",
            name="causal_root_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS outbox_causal_root_idx
                ON common_transactional_outbox (causal_root_id)
                WHERE causal_root_id IS NOT NULL;
            """,
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS outbox_causal_root_idx;",
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="outboxrecord",
                    index=models.Index(
                        condition=models.Q(causal_root_id__isnull=False),
                        fields=["causal_root_id"],
                        name="outbox_causal_root_idx",
                    ),
                ),
            ],
        ),
    ]
