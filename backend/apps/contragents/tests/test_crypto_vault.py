import pytest

from apps.common.security.pii_vault import PiiEncryptionVault


@pytest.mark.django_db
class TestPiiCryptoResilience:
    def test_pgp_sym_encrypt_uses_salt_and_generates_unique_ciphertexts(self):
        """[LEVEL 8: SECURITY] Проверка детерминированного соления OpenPGP AES-256 [1]."""
        raw_passport = "4510-777888"

        # Шифруем один и тот же паспорт дважды
        cipher_one = PiiEncryptionVault.encrypt_field(raw_passport)
        cipher_two = PiiEncryptionVault.encrypt_field(raw_passport)

        # Потоки байт в PostgreSQL обязаны различаться, защищая данные от утечки
        assert cipher_one != cipher_two, "CRITICAL: Отсутствует вектор инициализации (IV)! База уязвима."

        # Оба потока должны успешно расшифровываться обратно в исходную строку
        assert PiiEncryptionVault.decrypt_field(cipher_one) == raw_passport
        assert PiiEncryptionVault.decrypt_field(cipher_two) == raw_passport
