import re
from django.core.exceptions import ValidationError
from apps.contragents.models.contragent import ContragentType


class ContragentValidationPolicy:
    """
    [ADR 0030 / Пункт 26] Детерминированная матрица валидации контрагентов и ПДн граждан.
    Реализует инварианты верификации ИНН и жесткие ограничения файловой системы ЖКХ.
    """

    @staticmethod
    def verify_inn_compliance(contragent_type: str, inn: str) -> None:
        """
        [ИСПРАВЛЕНО] Жесткая проверка длины и символов ИНН.
        Вызывает ValidationError, блокируя мутацию на ранней стадии.
        """
        if not inn.isdigit():
            raise ValidationError("Формат ИНН нарушен: строка должна содержать только цифры.")

        # Проверка по матрице типов контрагентов [Пункт 26 аудита]
        if contragent_type == ContragentType.LEGAL_ENTITY and len(inn) != 10:
            raise ValidationError(
                f"Невалидный ИНН: Для юридического лица длина обязана быть 10 цифр. Получено: {len(inn)}."
            )

        if contragent_type in [ContragentType.ENTREPRENEUR, ContragentType.INDIVIDUAL] and len(inn) != 12:
            raise ValidationError(
                f"Невалидный ИНН: Для ИП или физлица длина обязана быть 12 цифр. Получено: {len(inn)}."
            )

    @staticmethod
    def verify_fio_regex(first_name: str, last_name: str) -> None:
        """Валидация синтаксиса имен (защита от спецсимволов и инъекций в FTS поиск)."""
        # Разрешена только кириллица, латиница, пробелы и дефисы
        fio_pattern = re.compile(r"^[А-Яа-яЁёA-Za-z\s\-]+$")
        if not fio_pattern.match(first_name) or not fio_pattern.match(last_name):
            raise ValidationError("Формат ФИО нарушен: обнаружены недопустимые символы.")

    @staticmethod
    def verify_attached_file_limits(file_size_bytes: int, mime_type: str) -> None:
        """
        [ИСПРАВЛЕНО] Жесткий контроль лимитов вложений по правилу ЖКХ-ERP.
        Максимальный размер — 5 МБ, форматы только PDF, JPG, PNG.
        """
        max_allowed_size = 5 * 1024 * 1024  # 5 Мегабайт в байтах
        if file_size_bytes > max_allowed_size:
            raise ValidationError("Размер прикрепляемого документа превышает лимит 5 МБ.")

        allowed_mime_types = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}

        if mime_type not in allowed_mime_types:
            raise ValidationError(f"Запрещенный тип файла: {mime_type}. Система принимает только PDF, JPG, PNG.")
