from django.db import migrations


def seed_director_dashboard_permission(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Role = apps.get_model("common", "Role")
    RolePermission = apps.get_model("common", "RolePermission")

    perm, _ = Permission.objects.get_or_create(action="read", resource="director_dashboard")

    for role_name in ("director", "admin"):
        role = Role.objects.filter(name=role_name).first()
        if role:
            RolePermission.objects.get_or_create(role=role, permission=perm)


def unseed_director_dashboard_permission(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    RolePermission = apps.get_model("common", "RolePermission")

    perm = Permission.objects.filter(action="read", resource="director_dashboard").first()
    if perm:
        RolePermission.objects.filter(permission=perm).delete()
        perm.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0025_seed_tasks_permissions"),
    ]

    operations = [
        migrations.RunPython(
            seed_director_dashboard_permission,
            unseed_director_dashboard_permission,
        ),
    ]
