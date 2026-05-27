import os
import structlog
from django.db import connection
from django.core.exceptions import PermissionDenied

logger = structlog.get_logger(__name__)


class PiiEncryptionVault:
    """
    [ADR 0009] Высокозащищенный криптографический сейф персональных данных по 152-ФЗ.
    [ИСПРАВЛЕНО] Использует стандарт OpenPGP (AES-256) с принудительным солением и MDC [1].
    """

    _cached_master_key: str | None = None

    @classmethod
    def _get_master_key_runtime(cls) -> str:
        """Извлекает текстовый ключ из Docker Secrets для OpenPGP функций [1]."""
        if cls._cached_master_key:
            return cls._cached_master_key

        secret_path = "/run/secrets/pii_master_key"

        if not os.path.exists(secret_path):
            if os.getenv("ENVIRONMENT") == "local":
                cls._cached_master_key = (
                    "local_dev_high_entropy_secure_vault_master_key_2026"
                )
                return cls._cached_master_key
            raise PermissionDenied(
                "CRITICAL SECURITY FAILURE: Docker Secret PII Key is missing!"
            )

        try:
            with open(secret_path, "r", encoding="utf-8") as secret_file:
                cls._cached_master_key = secret_file.read().strip()
                return cls._cached_master_key
        except Exception as exc:
            logger.critical("SECURITY VAULT OPENPGP READ FAILED", error=str(exc))
            raise PermissionDenied(
                "CRITICAL SECURITY FAILURE: Cannot unlock PII Vault Key."
            )

    @classmethod
    def encrypt_field(cls, raw_text: str) -> bytes:
        """
        [ИСПРАВЛЕНО] Симметричное шифрование OpenPGP AES-256.
        Автоматически генерирует уникальный вектор инициализации (IV) для каждой строки [1].
        """
        if not raw_text:
            return b""

        key = cls._get_master_key_runtime()
        # Принудительно заставляем pgcrypto использовать AES-256 вместо дефолтного AES-128 [1]
        query = "SELECT pgp_sym_encrypt(%s, %s, 'cipher-algo=aes256')"

        with connection.cursor() as cursor:
            cursor.execute(query, [raw_text, key])
            return cursor.fetchone()[0]

    @classmethod
    def decrypt_field(cls, cipher_bytes: bytes) -> str:
        """[ИСПРАВЛЕНО] Безопасное дешифрование OpenPGP байт с проверкой целостности блока [1]."""
        if not cipher_bytes:
            return ""

        key = cls._get_master_key_runtime()
        query = "SELECT pgp_sym_decrypt(%s, %s)"

        with connection.cursor() as cursor:
            try:
                cursor.execute(query, [cipher_bytes, key])
                return cursor.fetchone()[0]
            except Exception as exc:
                logger.error(
                    "PII DECRYPTION INTEGRITY FAILURE (MDC OR WRONG KEY)",
                    error=str(exc),
                )
                return ""
