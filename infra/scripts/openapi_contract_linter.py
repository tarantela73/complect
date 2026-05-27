import sys
import json
import os


def check_openapi_contract_compatibility():
    """
    [ИСПРАВЛЕНО] Автоматический СБ-валидатор обратной совместимости контрактов OpenAPI.
    Защищает фронтенд от ломающих изменений Pydantic DTO схем бэкенда.
    """
    current_schema_path = "backend/openapi.json"
    master_schema_path = "backend/openapi_master.json"

    if not os.path.exists(master_schema_path):
        print(
            "ℹ️ Master-схема отсутствует (первый запуск). Проверка совместимости пропущена."
        )
        sys.exit(0)

    with open(current_schema_path, "r", encoding="utf-8") as f:
        current = json.load(f)
    with open(master_schema_path, "r", encoding="utf-8") as f:
        master = json.load(f)

    breaking_changes = []

    # 1. Проверка удаления существующих путей/эндпоинтов
    for path, methods in master.get("paths", {}).items():
        if path not in current.get("paths", {}):
            breaking_changes.append(f"Удален эндпоинт: {path}")
            continue

        for method in methods:
            if method not in current["paths"][path]:
                breaking_changes.append(
                    f"Удален HTTP-метод {method.upper()} на эндпоинте {path}"
                )

    # 2. Проверка деградации схем Pydantic DTO моделей (components/schemas)
    for schema_name, master_schema in (
        master.get("components", {}).get("schemas", {}).items()
    ):
        current_schemas = current.get("components", {}).get("schemas", {})
        if schema_name not in current_schemas:
            breaking_changes.append(f"Удалена Pydantic DTO схема данных: {schema_name}")
            continue

        current_schema = current_schemas[schema_name]

        # Проверяем удаление полей из DTO схемы
        for prop in master_schema.get("properties", {}):
            if prop not in current_schema.get("properties", {}):
                breaking_changes.append(
                    f"Из DTO {schema_name} удалено обязательное поле: '{prop}'"
                )

    if breaking_changes:
        print("🚷 CONTRACT BREAKING CHANGES DETECTED:")
        for change in breaking_changes:
            print(f"  🚨 {change}")
        print("\nМердж заблокирован по правилу OpenAPI Freeze Policy [ADR 0028].")
        print("Создайте новую мажорную версию /api/v2/ или верните совместимость DTO.")
        sys.exit(1)

    print("✅ Схема OpenAPI полностью обратно совместима. Отсутствуют DTO Drift риски.")
    sys.exit(0)


if __name__ == "__main__":
    check_openapi_contract_compatibility()
