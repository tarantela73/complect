import ast
import os

import structlog
from django.core.exceptions import PermissionDenied
from django.db import connection

logger = structlog.get_logger(__name__)


class PiiEncryptionVault:
    @staticmethod
    def encode_for_text_field(ciphertext: bytes | str | memoryview) -> str:
        if isinstance(ciphertext, memoryview):
            ciphertext = ciphertext.tobytes()
        if isinstance(ciphertext, str):
            try:
                if ciphertext.startswith("\\x"):
                    bytes.fromhex(ciphertext[2:])
                elif ciphertext.startswith(("b'", 'b"')):
                    if not isinstance(ast.literal_eval(ciphertext), bytes):
                        raise ValueError
                elif ciphertext:
                    raise ValueError
            except (SyntaxError, ValueError):
                raise PermissionDenied("CRITICAL SECURITY FAILURE: invalid PII ciphertext") from None
            return ciphertext
        if not isinstance(ciphertext, bytes):
            raise PermissionDenied("CRITICAL SECURITY FAILURE: invalid PII ciphertext")
        if not ciphertext:
            return ""
        return "\\x" + ciphertext.hex()

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
                cls._cached_master_key = "local_dev_high_entropy_secure_vault_master_key_2026"
                return cls._cached_master_key
            raise PermissionDenied("CRITICAL SECURITY FAILURE: Docker Secret PII Key is missing!")

        try:
            with open(secret_path, "r", encoding="utf-8") as secret_file:
                cls._cached_master_key = secret_file.read().strip()
                return cls._cached_master_key
        except Exception as exc:
            logger.critical("SECURITY VAULT OPENPGP READ FAILED", error=str(exc))
            raise PermissionDenied("CRITICAL SECURITY FAILURE: Cannot unlock PII Vault Key.") from None

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
    def decrypt_field(cls, cipher_bytes: bytes | str | memoryview | None) -> str:
        """[ИСПРАВЛЕНО] Безопасное дешифрование OpenPGP байт с проверкой целостности блока [1]."""
        if not cipher_bytes:
            return ""
        if isinstance(cipher_bytes, memoryview):
            cipher_bytes = cipher_bytes.tobytes()
        elif isinstance(cipher_bytes, str):
            if cipher_bytes.startswith("\\x"):
                try:
                    cipher_bytes = bytes.fromhex(cipher_bytes[2:])
                except ValueError as exc:
                    raise PermissionDenied("CRITICAL SECURITY FAILURE: invalid PII ciphertext") from exc
            elif cipher_bytes.startswith("b'") or cipher_bytes.startswith('b"'):
                try:
                    parsed = ast.literal_eval(cipher_bytes)
                except (SyntaxError, ValueError) as exc:
                    raise PermissionDenied("CRITICAL SECURITY FAILURE: invalid PII ciphertext") from exc
                if not isinstance(parsed, bytes):
                    raise PermissionDenied("CRITICAL SECURITY FAILURE: invalid PII ciphertext")
                cipher_bytes = parsed
            else:
                raise PermissionDenied("CRITICAL SECURITY FAILURE: invalid PII ciphertext")

        if not cipher_bytes:
            return ""
        key = cls._get_master_key_runtime()
        query = "SELECT pgp_sym_decrypt(%s, %s)"

        try:
            with connection.cursor() as cursor:
                cursor.execute(query, [cipher_bytes, key])
                return cursor.fetchone()[0]
        except Exception:
            logger.error(
                "PII DECRYPTION INTEGRITY FAILURE (MDC OR WRONG KEY)",
            )
            raise PermissionDenied("PII_DECRYPTION_FAILED") from None
