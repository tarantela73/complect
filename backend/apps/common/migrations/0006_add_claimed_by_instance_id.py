from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0005_add_processed_event_acked_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="processedevent",
            name="claimed_by_instance_id",
            field=models.UUIDField(blank=True, null=True),
        ),
    ]
