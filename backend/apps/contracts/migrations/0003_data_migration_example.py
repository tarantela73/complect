from django.db import migrations


def batch_copy_contract_numbers(apps, schema_editor):
    """
    [SWITCH-ФАЗА] Пошаговое копирование данных батчами по 5000 строк.
    Исключает долгие блокировки строк и дает другим воркерам ASGI читать базу параллельно.
    """
    Contract = apps.get_model("contracts", "Contract")

    # Инициируем курсорный циклKeyset пагинации по ID [4.9]
    last_id = None
    batch_size = 5000

    while True:
        # Выбираем строго пачку необработанных записей
        queryset = Contract.objects.all().order_by("id")
        if last_id:
            queryset = queryset.filter(id__gt=last_id)

        batch = list(queryset[:batch_size])
        if not batch:
            break

        for contract in batch:
            # Логика маппинга данных
            if hasattr(contract, "old_number") and hasattr(contract, "contract_number"):
                contract.contract_number = contract.old_number
                # save() здесь безопасен, так как это изолированная дата-миграция
                contract.save(update_fields=["contract_number"])

        last_id = batch[-1].id


class Migration(migrations.Migration):
    dependencies = [
        ("contracts", "0002_enforce_rls_contracts"),
    ]

    operations = [
        # Запускаем безопасную Python-функцию миграции данных
        migrations.RunPython(
            batch_copy_contract_numbers, reverse_code=migrations.RunPython.noop
        ),
    ]
