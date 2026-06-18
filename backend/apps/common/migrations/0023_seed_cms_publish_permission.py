from django.db import migrations


def add_cms_publish_permission(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Role = apps.get_model("common", "Role")
    RolePermission = apps.get_model("common", "RolePermission")

    publish_perm, _ = Permission.objects.get_or_create(action="publish", resource="cms")

    for role_name in ("admin", "cms_editor"):
        role = Role.objects.filter(name=role_name).first()
        if role:
            RolePermission.objects.get_or_create(role=role, permission=publish_perm)


def remove_cms_publish_permission(apps, schema_editor):
    Permission = apps.get_model("common", "Permission")
    Permission.objects.filter(resource="cms", action="publish").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0022_seed_requests_permission"),
    ]

    operations = [
        migrations.RunPython(
            add_cms_publish_permission,
            reverse_code=remove_cms_publish_permission,
        ),
    ]
