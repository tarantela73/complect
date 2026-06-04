from django.db import migrations

# Canonical permission list — must stay in sync with rbac_permissions.py.
_ALL_PERMISSIONS = [
    ("read", "contracts"),
    ("create", "contracts"),
    ("write", "contracts"),
    ("read", "finance"),
    ("create", "finance"),
    ("write", "finance"),
    ("read", "objects"),
    ("create", "objects"),
    ("write", "objects"),
    ("delete", "objects"),
    ("read", "contragents"),
    ("create", "contragents"),
    ("write", "contragents"),
    ("delete", "contragents"),
    ("read", "users"),
    ("create", "users"),
    ("write", "users"),
    ("read", "files"),
    ("create", "files"),
    ("delete", "files"),
    ("download", "files"),
    ("read", "dlq"),
    ("write", "dlq"),
]

_MANAGER_PERMISSIONS = [
    ("read", "contracts"),
    ("create", "contracts"),
    ("write", "contracts"),
    ("read", "finance"),
    ("create", "finance"),
    ("read", "objects"),
    ("create", "objects"),
    ("write", "objects"),
    ("read", "contragents"),
    ("create", "contragents"),
    ("write", "contragents"),
    ("read", "files"),
    ("create", "files"),
    ("download", "files"),
    ("read", "users"),
]

_VIEWER_PERMISSIONS = [
    ("read", "contracts"),
    ("read", "finance"),
    ("read", "objects"),
    ("read", "contragents"),
    ("read", "files"),
    ("download", "files"),
    ("read", "users"),
]

_ROLES = [
    {
        "name": "admin",
        "description": "Полный доступ ко всем ресурсам системы",
        "permissions": _ALL_PERMISSIONS,
    },
    {
        "name": "manager",
        "description": "Создание и редактирование данных по договорам и контрагентам",
        "permissions": _MANAGER_PERMISSIONS,
    },
    {
        "name": "viewer",
        "description": "Только просмотр данных",
        "permissions": _VIEWER_PERMISSIONS,
    },
]


def seed_rbac(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Role = apps.get_model("common", "Role")
    RolePermission = apps.get_model("common", "RolePermission")

    perm_map = {}
    for action, resource in _ALL_PERMISSIONS:
        perm, _ = Permission.objects.get_or_create(action=action, resource=resource)
        perm_map[(action, resource)] = perm

    for role_def in _ROLES:
        role, _ = Role.objects.get_or_create(
            name=role_def["name"],
            defaults={"description": role_def["description"]},
        )
        for action, resource in role_def["permissions"]:
            perm = perm_map[(action, resource)]
            RolePermission.objects.get_or_create(role=role, permission=perm)


def unseed_rbac(apps, schema_editor):
    Role = apps.get_model("common", "Role")
    Permission = apps.get_model("common", "Permission")
    Role.objects.filter(name__in=["admin", "manager", "viewer"]).delete()
    Permission.objects.filter(
        action__in=["read", "create", "write", "delete", "download"]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0015_create_rbac"),
    ]

    operations = [
        migrations.RunPython(seed_rbac, reverse_code=unseed_rbac),
    ]
