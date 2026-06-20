from django.db import migrations


def add_files_delete_manager(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Role = apps.get_model("common", "Role")
    RolePermission = apps.get_model("common", "RolePermission")

    perm, _ = Permission.objects.get_or_create(action="delete", resource="files")

    manager_role = Role.objects.filter(name="manager").first()
    if manager_role:
        RolePermission.objects.get_or_create(role=manager_role, permission=perm)


def remove_files_delete_manager(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Role = apps.get_model("common", "Role")
    RolePermission = apps.get_model("common", "RolePermission")

    try:
        perm = Permission.objects.get(action="delete", resource="files")
        manager_role = Role.objects.get(name="manager")
        RolePermission.objects.filter(role=manager_role, permission=perm).delete()
    except (Permission.DoesNotExist, Role.DoesNotExist):
        pass


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0023_seed_cms_publish_permission"),
    ]

    operations = [
        migrations.RunPython(
            add_files_delete_manager,
            reverse_code=remove_files_delete_manager,
        ),
    ]
