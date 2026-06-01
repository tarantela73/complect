from django.conf import settings


class TestPiiSecretsGovernance:
    def test_pii_master_key_is_not_stored_in_django_settings(self):
        """[LEVEL 8: SECURITY] Гарантирует отсутствие конфиденциальных ключей в кодовой базе настроек."""
        forbidden_keys = ["PII_MASTER_KEY", "PII_KEY", "MASTER_KEY", "PASSPORT_KEY"]

        for key in forbidden_keys:
            # Сборка падает, если ключ обнаружен в глобальном пространстве настроек Django
            assert not hasattr(settings, key), (
                f"CRITICAL SECURITY HOLE: {key} найден в settings.py!"
            )