from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False  # CONCURRENTLY cannot run inside a transaction

    dependencies = [
        ("finance", "0003_add_value_date_and_inn"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="transaction",
            index=models.Index(
                fields=["tenant_id", "contract_id", "status", "is_deleted", "-id"],
                name="idx_tx_360_payments",
            ),
        ),
    ]
