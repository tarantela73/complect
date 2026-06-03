from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("common", "0006_add_claimed_by_instance_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="processedevent",
            name="lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS processed_event_lease_idx
                ON common_processed_events (consumer, lease_expires_at)
                WHERE acked_at IS NULL AND lease_expires_at IS NOT NULL;
            """,
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS processed_event_lease_idx;",
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="processedevent",
                    index=models.Index(
                        condition=models.Q(acked_at__isnull=True, lease_expires_at__isnull=False),
                        fields=["consumer", "lease_expires_at"],
                        name="processed_event_lease_idx",
                    ),
                ),
            ],
        ),
    ]
