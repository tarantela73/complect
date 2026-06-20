from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("objects", "0004_object_contract"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplyobject",
            name="is_public",
            field=models.BooleanField(default=False, db_index=True),
        ),
        migrations.AddField(
            model_name="supplyobject",
            name="slug",
            field=models.SlugField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="supplyobject",
            name="cover_image_key",
            field=models.CharField(max_length=500, null=True, blank=True),
        ),
        migrations.AddIndex(
            model_name="supplyobject",
            index=models.Index(fields=["slug"], name="idx_objects_slug"),
        ),
    ]
