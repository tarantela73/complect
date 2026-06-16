from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("requests", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="request",
            name="resource_type",
            field=models.CharField(
                choices=[
                    ("ELECTRICITY", "Электроснабжение"),
                    ("GAS", "Газоснабжение"),
                    ("WATER", "Водоснабжение"),
                    ("HEAT", "Теплоснабжение"),
                    ("SEWAGE", "Водоотведение"),
                    ("CONSTRUCTION", "Строительство"),
                    ("OTHER", "Прочее"),
                ],
                db_index=True,
                default="OTHER",
                max_length=30,
            ),
        ),
    ]
