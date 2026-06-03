import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0013_create_outbox_archive"),
    ]

    operations = [
        migrations.CreateModel(
            name="CausalPairRepairRecord",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("pair_name", models.CharField(max_length=255)),
                ("source_event_id", models.UUIDField()),
                ("source_event_type", models.CharField(max_length=255)),
                ("expected_event_type", models.CharField(max_length=255)),
                ("divergence_state", models.CharField(max_length=16)),
                ("repair_initiated_at", models.DateTimeField(auto_now_add=True)),
                ("repair_initiated_by", models.CharField(max_length=255)),
                ("repair_completed_at", models.DateTimeField(blank=True, null=True)),
                ("compensating_transaction_id", models.UUIDField(blank=True, null=True)),
                ("sign_off_by", models.CharField(blank=True, max_length=255, null=True)),
                ("sign_off_at", models.DateTimeField(blank=True, null=True)),
                ("notes", models.TextField(blank=True, null=True)),
            ],
            options={
                "db_table": "common_causal_pair_repair_records",
            },
        ),
        migrations.AddIndex(
            model_name="causalpairrepairrecord",
            index=models.Index(
                fields=["source_event_id"],
                name="cprepair_source_idx",
            ),
        ),
    ]
