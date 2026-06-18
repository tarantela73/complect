from django.db import migrations, models


def backfill_status(apps, schema_editor):
    schema_editor.execute(
        "UPDATE site_page_content SET status = 'PUBLISHED' WHERE is_deleted = FALSE AND status IS NULL"
    )
    schema_editor.execute(
        "UPDATE site_page_content SET status = 'DRAFT' WHERE is_deleted = TRUE AND status IS NULL"
    )


def reverse_backfill_status(apps, schema_editor):
    schema_editor.execute(
        "UPDATE site_page_content SET status = NULL WHERE TRUE"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("site", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitepagecontent",
            name="status",
            field=models.CharField(
                blank=True,
                choices=[("DRAFT", "Draft"), ("PUBLISHED", "Published"), ("SCHEDULED", "Scheduled")],
                db_index=True,
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="sitepagecontent",
            name="publish_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_status, reverse_code=reverse_backfill_status),
    ]
