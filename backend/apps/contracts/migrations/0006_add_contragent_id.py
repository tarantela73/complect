from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    [ADR-0022, ADR-0002] Release N — Expand phase.
    Adds nullable contragent_id to contracts_contracts + composite index.
    atomic=False required for CONCURRENTLY index creation.
    """

    atomic = False

    dependencies = [
        ("contracts", "0005_search_vector"),
    ]

    operations = [
        migrations.AddField(
            model_name="contract",
            name="contragent_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        AddIndexConcurrently(
            model_name="contract",
            index=models.Index(
                fields=["tenant_id", "contragent_id", "status", "is_deleted", "-id"],
                name="idx_contract_tenant_contragent",
            ),
        ),
    ]
