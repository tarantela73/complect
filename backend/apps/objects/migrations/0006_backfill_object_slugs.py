from django.db import migrations
from django.utils.text import slugify


def _unique_slug_candidate(base, used, max_length):
    candidate = base[:max_length]
    n = 2
    while candidate in used:
        suffix = f"-{n}"
        if len(suffix) > max_length:
            raise ValueError(f"Slug collision suffix exceeds max_length={max_length}")
        candidate = f"{base[:max_length - len(suffix)]}{suffix}"
        n += 1
    return candidate


def backfill_slugs(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    SupplyObject = apps.get_model("objects", "SupplyObject")
    objects = SupplyObject.objects.using(db_alias)
    max_length = SupplyObject._meta.get_field("slug").max_length

    pending = list(
        objects.filter(is_deleted=False, slug__isnull=True)
        .values("id", "name", "tenant_id")
        .order_by("tenant_id", "id")
    )

    # Track used slugs per tenant to avoid duplicates during backfill
    used: dict = {}  # {tenant_id: set[slug]}

    for row in pending:
        tenant_id = row["tenant_id"]
        base = slugify(row["name"], allow_unicode=True) or "object"

        if tenant_id not in used:
            existing = set(
                objects.filter(
                    tenant_id=tenant_id, is_deleted=False, slug__isnull=False
                ).values_list("slug", flat=True)
            )
            used[tenant_id] = existing

        candidate = _unique_slug_candidate(base, used[tenant_id], max_length)

        used[tenant_id].add(candidate)
        objects.filter(id=row["id"]).update(slug=candidate)


class Migration(migrations.Migration):

    dependencies = [
        ("objects", "0005_add_public_fields"),
    ]

    operations = [
        migrations.RunPython(
            backfill_slugs,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
