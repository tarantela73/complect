import pytest
from django.core.exceptions import ValidationError

from apps.contragents.models.contragent import ContragentType
from apps.contragents.policies.validation_matrix import ContragentValidationPolicy


class TestContragentValidationPolicySuite:
    def test_inn_validation_matrix_accepts_valid_data(self):
        """Проверяет успешное прохождение валидных ИНН через матрицу."""
        # Юрлицо — 10 цифр
        ContragentValidationPolicy.verify_inn_compliance(ContragentType.LEGAL_ENTITY, "7701020304")
        # ИП — 12 цифр
        ContragentValidationPolicy.verify_inn_compliance(ContragentType.ENTREPRENEUR, "770102030405")

    def test_inn_validation_matrix_rejects_malformed_lengths(self):
        """Проверяет блокировку некорректных длин ИНН."""
        # Попытка подсунуть 12 цифр для ООО должна упасть
        with pytest.raises(ValidationError, match="длина обязана быть 10 цифр"):
            ContragentValidationPolicy.verify_inn_compliance(ContragentType.LEGAL_ENTITY, "770102030405")

        # Попытка подсунуть буквы
        with pytest.raises(ValidationError, match="должна содержать только цифры"):
            ContragentValidationPolicy.verify_inn_compliance(ContragentType.INDIVIDUAL, "7701020304AA")

    def test_file_restrictions_enforce_size_and_mimes(self):
        """Проверяет защиту от заливки тяжелых файлов и опасных бинарников."""
        # 2 МБ PDF — проходит успешно
        ContragentValidationPolicy.verify_attached_file_limits(2 * 1024 * 1024, "application/pdf")

        # 6 МБ JPG — должно заблокироваться
        with pytest.raises(ValidationError, match="превышает лимит 5 МБ"):
            ContragentValidationPolicy.verify_attached_file_limits(6 * 1024 * 1024, "image/jpeg")

        # Опасный исполняемый exe файл — должен моментально отбиться
        with pytest.raises(ValidationError, match="Запрещенный тип файла"):
            ContragentValidationPolicy.verify_attached_file_limits(1024, "application/x-msdownload")
