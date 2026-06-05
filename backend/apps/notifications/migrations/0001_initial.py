import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="InAppNotification",
            fields=[
                ("id", models.UUIDField(default=None, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                ("version", models.PositiveIntegerField(default=1)),
                ("tenant_id", models.UUIDField(db_index=True)),
                ("is_legal_hold", models.BooleanField(default=False)),
                ("user_id", models.UUIDField()),
                ("title", models.CharField(max_length=255)),
                ("body", models.TextField()),
                ("event_type", models.CharField(max_length=100)),
                ("payload", models.JSONField(default=dict)),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("archived_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "notifications_in_app",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="NotificationPreference",
            fields=[
                ("id", models.UUIDField(default=None, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                ("version", models.PositiveIntegerField(default=1)),
                ("tenant_id", models.UUIDField(db_index=True)),
                ("is_legal_hold", models.BooleanField(default=False)),
                ("user_id", models.UUIDField()),
                ("event_type", models.CharField(max_length=100)),
                ("email_enabled", models.BooleanField(default=False)),
                ("in_app_enabled", models.BooleanField(default=True)),
            ],
            options={
                "db_table": "notifications_preferences",
            },
        ),
        migrations.AddIndex(
            model_name="inappnotification",
            index=models.Index(
                fields=["tenant_id", "user_id", "-created_at"],
                name="notif_inapp_tenant_user_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="inappnotification",
            index=models.Index(
                fields=["tenant_id", "user_id", "read_at"],
                name="notif_inapp_unread_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="notificationpreference",
            constraint=models.UniqueConstraint(
                fields=["tenant_id", "user_id", "event_type"],
                name="uq_notif_pref_user_event",
            ),
        ),
    ]
