from django.db import migrations
from django.utils.text import slugify


def backfill_slugs(apps, schema_editor):
    SupplyObject = apps.get_model("objects", "SupplyObject")

    pending = list(
        SupplyObject.objects.filter(is_deleted=False, slug__isnull=True)
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
                SupplyObject.objects.filter(
                    tenant_id=tenant_id, is_deleted=False, slug__isnull=False
                ).values_list("slug", flat=True)
            )
            used[tenant_id] = existing

        candidate = base
        n = 2
        while candidate in used[tenant_id]:
            candidate = f"{base}-{n}"
            n += 1

        used[tenant_id].add(candidate)
        SupplyObject.objects.filter(id=row["id"]).update(slug=candidate)


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
