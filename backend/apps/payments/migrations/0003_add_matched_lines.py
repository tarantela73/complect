from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0002_add_match_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="bankstatement",
            name="matched_lines",
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
