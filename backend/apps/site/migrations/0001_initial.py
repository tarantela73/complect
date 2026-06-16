import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SitePageContent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tenant_id", models.UUIDField(db_index=True, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                ("version", models.PositiveIntegerField(default=1, editable=False)),
                ("is_legal_hold", models.BooleanField(db_index=True, default=False)),
                ("slug", models.CharField(db_index=True, max_length=50)),
                ("content", models.JSONField(default=dict)),
                ("meta", models.JSONField(default=dict)),
            ],
            options={
                "db_table": "site_page_content",
            },
        ),
        migrations.CreateModel(
            name="SiteImage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tenant_id", models.UUIDField(db_index=True, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                ("version", models.PositiveIntegerField(default=1, editable=False)),
                ("is_legal_hold", models.BooleanField(db_index=True, default=False)),
                ("section", models.CharField(db_index=True, max_length=50)),
                ("s3_key", models.CharField(max_length=500)),
                ("caption", models.CharField(blank=True, default="", max_length=255)),
                ("order", models.PositiveSmallIntegerField(default=0)),
            ],
            options={
                "db_table": "site_images",
                "ordering": ["order", "created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="sitepagecontent",
            constraint=models.UniqueConstraint(
                condition=models.Q(is_deleted=False),
                fields=["tenant_id", "slug"],
                name="unique_site_page_content_tenant_slug",
            ),
        ),
    ]
