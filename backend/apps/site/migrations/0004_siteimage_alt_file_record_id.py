from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("site", "0003_sitepagerevision"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteimage",
            name="alt",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="siteimage",
            name="file_record_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
    ]
