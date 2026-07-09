#!/usr/bin/env python
import os
import sys
from pathlib import Path


def _load_dotenv() -> None:
    """Загружает .env из корня репозитория при запуске вне Docker."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)


def main():
    """Единая точка управления миграциями и проверками ERP Комплект."""
    _load_dotenv()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Не удалось импортировать Django. Убедитесь, что оно установлено "
            "и ваше виртуальное окружение .venv активировано."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
