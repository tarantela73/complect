import sys
import os
import re


def run_migration_security_linter():
    """
    [ИСПРАВЛЕНО] Автоматический СБ-линтер мигpаций СУБД Комплекса.
    Сканирует новые файлы миграций на наличие опасных блокировок.
    """
    migration_dir = "backend/apps/"
    destructive_patterns = [
        (
            re.compile(r"RemoveField"),
            "Удаление полей запрещено. Используйте Contract-фазу через 2 релиза.",
        ),
        (
            re.compile(r"AlterField"),
            "Прямое изменение типа поля запрещено. Используйте схему миграции данных.",
        ),
        (
            re.compile(r"AddField.*null=False(?!.*default=)"),
            "Добавление NOT NULL поля без дефолта вызовет блокировку AccessExclusiveLock!",
        ),
    ]

    failed = False
    for root, _, files in os.walk(migration_dir):
        if "migrations" in root:
            for file in files:
                if file.endswith(".py") and file != "__init__.py":
                    file_path = os.path.join(root, file)
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        for pattern, error_msg in destructive_patterns:
                            if pattern.search(content):
                                print(
                                    f"🚨 QUALITY GATE FAILED: {file_path} -> {error_msg}"
                                )
                                failed = True

    if failed:
        print("🚷 Мердж заблокирован. Исправьте миграции согласно ADR 0027.")
        sys.exit(1)
    print("✅ Все миграции соответствуют правилу Zero-Downtime Expand & Contract.")


if __name__ == "__main__":
    run_migration_security_linter()
