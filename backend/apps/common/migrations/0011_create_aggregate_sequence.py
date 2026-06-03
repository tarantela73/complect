from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0010_add_outbox_aggregate_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="AggregateSequence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("tenant_id", models.UUIDField()),
                ("aggregate_type", models.CharField(max_length=255)),
                ("aggregate_id", models.UUIDField()),
                ("current_seq", models.BigIntegerField(default=0)),
            ],
            options={
                "db_table": "common_aggregate_sequences",
            },
        ),
        migrations.AddConstraint(
            model_name="aggregatesequence",
            constraint=models.UniqueConstraint(
                fields=["tenant_id", "aggregate_type", "aggregate_id"],
                name="aggregate_sequence_natural_pk",
            ),
        ),
    ]
