from django.db import migrations


def add_webhooks_permissions(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Role = apps.get_model("common", "Role")
    RolePermission = apps.get_model("common", "RolePermission")

    read_perm, _ = Permission.objects.get_or_create(action="read", resource="webhooks")
    write_perm, _ = Permission.objects.get_or_create(action="write", resource="webhooks")

    for role_name in ("admin", "manager"):
        role = Role.objects.filter(name=role_name).first()
        if role:
            RolePermission.objects.get_or_create(role=role, permission=read_perm)
            RolePermission.objects.get_or_create(role=role, permission=write_perm)


def remove_webhooks_permissions(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Permission.objects.filter(resource="webhooks").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0019_seed_ops_permission"),
    ]

    operations = [
        migrations.RunPython(
            add_webhooks_permissions,
            reverse_code=remove_webhooks_permissions,
        ),
    ]
