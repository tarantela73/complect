import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0003_add_outbox_backoff"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProcessedEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("outbox_record_id", models.UUIDField()),
                ("consumer", models.CharField(max_length=255)),
                ("tenant_id", models.UUIDField()),
                ("processed_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "common_processed_events",
            },
        ),
        migrations.AddConstraint(
            model_name="processedevent",
            constraint=models.UniqueConstraint(
                fields=["outbox_record_id", "consumer"],
                name="processed_event_consumer_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="processedevent",
            index=models.Index(
                fields=["consumer", "tenant_id", "processed_at"],
                name="processed_event_lookup_idx",
            ),
        ),
    ]
