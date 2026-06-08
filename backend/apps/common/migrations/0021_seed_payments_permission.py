from django.db import migrations


def add_payments_permissions(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Role = apps.get_model("common", "Role")
    RolePermission = apps.get_model("common", "RolePermission")

    read_perm, _ = Permission.objects.get_or_create(action="read", resource="payments")
    write_perm, _ = Permission.objects.get_or_create(action="write", resource="payments")

    for role_name in ("admin", "manager"):
        role = Role.objects.filter(name=role_name).first()
        if role:
            RolePermission.objects.get_or_create(role=role, permission=read_perm)
            RolePermission.objects.get_or_create(role=role, permission=write_perm)


def remove_payments_permissions(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Permission.objects.filter(resource="payments").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0020_seed_webhooks_permission"),
    ]

    operations = [
        migrations.RunPython(
            add_payments_permissions,
            reverse_code=remove_payments_permissions,
        ),
    ]
