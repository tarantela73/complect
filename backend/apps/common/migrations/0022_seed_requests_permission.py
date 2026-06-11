from django.db import migrations


def add_requests_permissions(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Role = apps.get_model("common", "Role")
    RolePermission = apps.get_model("common", "RolePermission")

    read_perm, _ = Permission.objects.get_or_create(action="read", resource="requests")
    write_perm, _ = Permission.objects.get_or_create(action="write", resource="requests")

    for role_name in ("admin", "director", "manager"):
        role = Role.objects.filter(name=role_name).first()
        if role:
            RolePermission.objects.get_or_create(role=role, permission=read_perm)
            RolePermission.objects.get_or_create(role=role, permission=write_perm)


def remove_requests_permissions(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Permission.objects.filter(resource="requests").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0021_seed_payments_permission"),
    ]

    operations = [
        migrations.RunPython(
            add_requests_permissions,
            reverse_code=remove_requests_permissions,
        ),
    ]
