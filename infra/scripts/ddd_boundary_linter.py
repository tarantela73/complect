import ast
import os
import sys


def check_ddd_boundary_violations():
    """
    [ИСПРАВЛЕНО] Автоматический AST-линтер архитектурных границ Bounded Contexts.
    Намертво выжигает кросс-импорты моделей, предотвращая превращение монолита в спагетти.
    """
    base_apps_dir = "backend/apps"
    violations = []

    # Сканируем структуру доменных приложений бэкенда
    for root, _, files in os.walk(base_apps_dir):
        for file in files:
            if file.endswith(".py") and "migrations" not in root:
                file_path = os.path.join(root, file)

                # Определяем, какому домену принадлежит текущий файл (например, 'contracts')
                relative_path = os.relpath(file_path, base_apps_dir)
                path_parts = relative_path.split(os.sep)
                if not path_parts or path_parts[0] in ["common", "config"]:
                    continue
                current_domain = path_parts[0]

                with open(file_path, "r", encoding="utf-8") as f:
                    try:
                        node = ast.parse(f.read(), filename=file_path)
                    except SyntaxError:
                        continue

                    # Анализируем все импорты в текущем файле кода
                    for subnode in ast.walk(node):
                        imported_module = None

                        if isinstance(subnode, ast.ImportFrom) and subnode.module:
                            imported_module = subnode.module
                        elif isinstance(subnode, ast.Import):
                            imported_module = subnode.names[0].name

                        if imported_module and imported_module.startswith("apps."):
                            import_parts = imported_module.split(".")
                            if len(import_parts) > 1:
                                target_domain = import_parts[1]

                                # Исключаем импорты из шаренной зоны common
                                if target_domain in [
                                    "common",
                                    "config",
                                    current_domain,
                                ]:
                                    continue

                                # [ГЛАВНОЕ ПРАВИЛО КРИТИЧНОСТИ]: Если обнаружен импорт моделей/сервисов чужого домена напрямую
                                if (
                                    "models" in import_parts
                                    or "services" in import_parts
                                ):
                                    violations.append(
                                        f"Файл '{file_path}' (Домен: {current_domain}) напрямую импортирует "
                                        f"внутренности чужого домена '{target_domain}' -> ({imported_module})."
                                    )

    if violations:
        print(
            "🚷 ARCHITECTURAL BOUNDARY VIOLATIONS DETECTED (EVENTUAL SPAGHETTI BLOCKER):"
        )
        for violation in violations:
            print(f"  🚨 {violation}")
        print(
            "\nМердж заблокирован по правилу Bounded-Context Ownership Rules [ADR 0030]."
        )
        print(
            "Используйте слои 'apps/common/contracts/' (Internal SDK) или Transactional Outbox события."
        )
        sys.exit(1)

    print("✅ Свод правил DDD полностью соблюден. Спагетти-связи отсутствуют.")
    sys.exit(0)


if __name__ == "__main__":
    check_ddd_boundary_violations()
