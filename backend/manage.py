#!/usr/bin/env python
import os
import sys

def main():
    """Единая точка управления миграциями и проверками ERP Комплект."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Не удалось импортировать Django. Убедитесь, что оно установлено "
            "и ваше виртуальное окружение .venv активировано."
        ) from exc
    execute_from_command_line(sys.argv)

if __name__ == '__main__':
    main()
