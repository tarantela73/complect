from django.db import migrations


def add_ops_permission(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Role = apps.get_model("common", "Role")
    RolePermission = apps.get_model("common", "RolePermission")

    perm, _ = Permission.objects.get_or_create(action="read", resource="ops")

    # ops.read → admin role only (platform-level visibility, not tenant business role)
    role = Role.objects.filter(name="admin").first()
    if role:
        RolePermission.objects.get_or_create(role=role, permission=perm)


def remove_ops_permission(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Permission.objects.filter(action="read", resource="ops").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0018_seed_notifications_permission"),
    ]

    operations = [
        migrations.RunPython(
            add_ops_permission,
            reverse_code=remove_ops_permission,
        ),
    ]
