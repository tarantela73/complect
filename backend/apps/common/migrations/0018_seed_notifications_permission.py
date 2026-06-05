from django.db import migrations


def add_notifications_permission(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Role = apps.get_model("common", "Role")
    RolePermission = apps.get_model("common", "RolePermission")

    perm, _ = Permission.objects.get_or_create(action="read", resource="notifications")

    for role_name in ("admin", "manager", "viewer"):
        role = Role.objects.filter(name=role_name).first()
        if role:
            RolePermission.objects.get_or_create(role=role, permission=perm)


def remove_notifications_permission(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Permission.objects.filter(action="read", resource="notifications").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0017_seed_dashboard_permission"),
    ]

    operations = [
        migrations.RunPython(
            add_notifications_permission,
            reverse_code=remove_notifications_permission,
        ),
    ]
