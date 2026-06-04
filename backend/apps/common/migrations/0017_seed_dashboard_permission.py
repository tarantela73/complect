from django.db import migrations


def add_dashboard_permission(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Role = apps.get_model("common", "Role")
    RolePermission = apps.get_model("common", "RolePermission")

    perm, _ = Permission.objects.get_or_create(action="read", resource="dashboard")

    for role_name in ("admin", "manager", "viewer"):
        role = Role.objects.filter(name=role_name).first()
        if role:
            RolePermission.objects.get_or_create(role=role, permission=perm)


def remove_dashboard_permission(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Permission.objects.filter(action="read", resource="dashboard").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0016_seed_rbac_data"),
    ]

    operations = [
        migrations.RunPython(add_dashboard_permission, reverse_code=remove_dashboard_permission),
    ]
