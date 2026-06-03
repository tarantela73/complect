from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("common", "0008_add_outbox_causal_root_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="processedevent",
            name="causal_root_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS pe_causal_root_idx
                ON common_processed_events (causal_root_id)
                WHERE causal_root_id IS NOT NULL;
            """,
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS pe_causal_root_idx;",
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="processedevent",
                    index=models.Index(
                        condition=models.Q(causal_root_id__isnull=False),
                        fields=["causal_root_id"],
                        name="pe_causal_root_idx",
                    ),
                ),
            ],
        ),
    ]
