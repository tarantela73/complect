from django.db import migrations

# Permissions the director role should hold.
# Mirrors what 0022/0025/0026 tried to assign but silently skipped because
# the role didn't exist yet.
_DIRECTOR_PERMISSIONS = [
    # Core read access (supervision)
    ("read", "dashboard"),
    ("read", "director_dashboard"),
    ("read", "contracts"),
    ("read", "contragents"),
    ("read", "finance"),
    ("read", "objects"),
    ("read", "files"),
    ("download", "files"),
    ("read", "notifications"),
    ("read", "payments"),
    ("read", "users"),
    ("read", "webhooks"),
    # Operational write
    ("read", "requests"),
    ("write", "requests"),
    ("read", "tasks"),
    ("write", "tasks"),
    ("delete", "tasks"),
]


def seed_director_role(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Role = apps.get_model("common", "Role")
    RolePermission = apps.get_model("common", "RolePermission")

    role, _ = Role.objects.get_or_create(
        name="director",
        defaults={"description": "Просмотр всех данных, управление задачами и поручениями"},
    )

    for action, resource in _DIRECTOR_PERMISSIONS:
        perm = Permission.objects.filter(action=action, resource=resource).first()
        if perm:
            RolePermission.objects.get_or_create(role=role, permission=perm)


def unseed_director_role(apps, schema_editor):
    Role = apps.get_model("common", "Role")
    RoleBinding = apps.get_model("common", "RoleBinding")
    RolePermission = apps.get_model("common", "RolePermission")

    role_qs = Role.objects.filter(name="director")
    RoleBinding.objects.filter(role__in=role_qs).delete()
    RolePermission.objects.filter(role__in=role_qs).delete()
    role_qs.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0026_seed_director_dashboard_permission"),
    ]

    operations = [
        migrations.RunPython(seed_director_role, reverse_code=unseed_director_role),
    ]
