from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0012_create_aggregate_sequence_fence"),
    ]

    operations = [
        migrations.CreateModel(
            name="OutboxArchive",
            fields=[
                ("id", models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ("event_type", models.CharField(max_length=255)),
                ("payload_json", models.JSONField()),
                ("correlation_id", models.UUIDField()),
                ("causal_root_id", models.UUIDField(blank=True, null=True)),
                ("aggregate_id", models.UUIDField(blank=True, null=True)),
                ("aggregate_type", models.CharField(blank=True, max_length=255, null=True)),
                ("aggregate_seq", models.BigIntegerField(blank=True, null=True)),
                ("is_processed", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField()),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("retry_count", models.PositiveSmallIntegerField(default=0)),
                ("last_error", models.TextField(blank=True, null=True)),
                ("failed_permanently_at", models.DateTimeField(blank=True, null=True)),
                ("next_retry_at", models.DateTimeField(blank=True, null=True)),
                ("archived_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "common_outbox_archive",
            },
        ),
    ]
