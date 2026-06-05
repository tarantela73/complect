import django.contrib.postgres.search
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("files", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="filerecord",
            name="search_vector",
            field=models.GeneratedField(
                db_persist=True,
                expression=django.contrib.postgres.search.SearchVector(
                    "original_name", config="pg_catalog.russian", weight="B"
                ),
                output_field=django.contrib.postgres.search.SearchVectorField(),
            ),
        ),
        AddIndexConcurrently(
            model_name="filerecord",
            index=GinIndex(fields=["search_vector"], name="files_search_gin"),
        ),
    ]
