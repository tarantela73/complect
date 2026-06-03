from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("finance", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS finance_tx_external_ref_uq
                ON finance_transactions (tenant_id, external_ref)
                WHERE external_ref != '';
            """,
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS finance_tx_external_ref_uq;",
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddConstraint(
                    model_name="transaction",
                    constraint=models.UniqueConstraint(
                        condition=~models.Q(external_ref=""),
                        fields=["tenant_id", "external_ref"],
                        name="finance_tx_external_ref_uq",
                    ),
                ),
            ],
        ),
    ]
