from django.db import migrations


def seed_tasks_permissions(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Role = apps.get_model("common", "Role")
    RolePermission = apps.get_model("common", "RolePermission")

    for action in ["read", "write", "delete"]:
        Permission.objects.get_or_create(action=action, resource="tasks")

    read_perm = Permission.objects.get(action="read", resource="tasks")
    write_perm = Permission.objects.get(action="write", resource="tasks")
    delete_perm = Permission.objects.get(action="delete", resource="tasks")

    role_perms = {
        "director": [read_perm, write_perm, delete_perm],
        "manager": [read_perm, write_perm],
        "accountant": [read_perm, write_perm],
        "foreman": [read_perm],
    }

    for role_name, perms in role_perms.items():
        role = Role.objects.filter(name=role_name).first()
        if not role:
            continue
        for perm in perms:
            RolePermission.objects.get_or_create(role=role, permission=perm)


def unseed_tasks_permissions(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    RolePermission = apps.get_model("common", "RolePermission")

    task_perms = Permission.objects.filter(resource="tasks")
    RolePermission.objects.filter(permission__in=task_perms).delete()
    task_perms.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0024_seed_files_delete_manager"),
    ]

    operations = [
        migrations.RunPython(seed_tasks_permissions, unseed_tasks_permissions),
    ]
