import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("site", "0002_sitepagecontent_status_publish_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="SitePageRevision",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tenant_id", models.UUIDField(db_index=True, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                ("version", models.PositiveIntegerField(default=1, editable=False)),
                ("is_legal_hold", models.BooleanField(db_index=True, default=False)),
                ("page_id", models.UUIDField(db_index=True)),
                ("revision_number", models.PositiveIntegerField()),
                ("content", models.JSONField()),
                ("meta", models.JSONField()),
                ("status", models.CharField(blank=True, default="", max_length=20)),
                ("created_by_id", models.UUIDField()),
                ("label", models.CharField(blank=True, default="", max_length=200)),
            ],
            options={
                "db_table": "site_page_revisions",
            },
        ),
        migrations.AddIndex(
            model_name="sitepagerevision",
            index=models.Index(
                fields=["tenant_id", "page_id", "revision_number"],
                name="idx_revision_tenant_page_num",
            ),
        ),
        migrations.AddConstraint(
            model_name="sitepagerevision",
            constraint=models.UniqueConstraint(
                fields=["tenant_id", "page_id", "revision_number"],
                name="uniq_revision_per_page",
            ),
        ),
    ]
