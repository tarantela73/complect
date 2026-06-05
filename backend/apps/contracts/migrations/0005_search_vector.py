import django.contrib.postgres.search
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("contracts", "0004_contract_attachments"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="contractattachment",
            name="ca_tenant_created_idx",
        ),
        migrations.RemoveIndex(
            model_name="contractattachment",
            name="ca_tenant_deleted_idx",
        ),
        migrations.RemoveIndex(
            model_name="contractattachment",
            name="ca_tenant_id_idx",
        ),
        migrations.RemoveIndex(
            model_name="contractattachment",
            name="ca_tenant_cursor_idx",
        ),
        migrations.AddField(
            model_name="contract",
            name="search_vector",
            field=models.GeneratedField(
                db_persist=True,
                expression=django.contrib.postgres.search.CombinedSearchVector(
                    django.contrib.postgres.search.SearchVector("contract_number", config="simple", weight="A"),
                    "||",
                    django.contrib.postgres.search.SearchVector("title", config="pg_catalog.russian", weight="B"),
                    django.contrib.postgres.search.SearchConfig("simple"),
                ),
                output_field=django.contrib.postgres.search.SearchVectorField(),
            ),
        ),
        AddIndexConcurrently(
            model_name="contract",
            index=GinIndex(fields=["search_vector"], name="contracts_search_gin"),
        ),
    ]
