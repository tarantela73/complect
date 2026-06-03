from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0011_create_aggregate_sequence"),
    ]

    operations = [
        migrations.CreateModel(
            name="AggregateSequenceFence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("tenant_id", models.UUIDField()),
                ("aggregate_type", models.CharField(max_length=255)),
                ("aggregate_id", models.UUIDField()),
                ("consumer", models.CharField(max_length=255)),
                ("last_seen_seq", models.BigIntegerField(default=0)),
                ("updated_at", models.DateTimeField()),
            ],
            options={
                "db_table": "common_aggregate_sequence_fences",
            },
        ),
        migrations.AddConstraint(
            model_name="aggregatesequencefence",
            constraint=models.UniqueConstraint(
                fields=["tenant_id", "aggregate_type", "aggregate_id", "consumer"],
                name="aggregate_sequence_fence_natural_pk",
            ),
        ),
    ]
