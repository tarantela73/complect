import django.contrib.postgres.search
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("contragents", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="contragent",
            name="search_vector",
            field=models.GeneratedField(
                db_persist=True,
                expression=django.contrib.postgres.search.CombinedSearchVector(
                    django.contrib.postgres.search.SearchVector("name", config="pg_catalog.russian", weight="B"),
                    "||",
                    django.contrib.postgres.search.SearchVector("inn", config="simple", weight="A"),
                    django.contrib.postgres.search.SearchConfig("pg_catalog.russian"),
                ),
                output_field=django.contrib.postgres.search.SearchVectorField(),
            ),
        ),
        AddIndexConcurrently(
            model_name="contragent",
            index=GinIndex(fields=["search_vector"], name="contragents_search_gin"),
        ),
    ]
