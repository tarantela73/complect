from django.apps import AppConfig


class FilesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.files"

    def ready(self) -> None:
        from config.legacy_document_compatibility import (
            register_contract_read_providers,
            register_legacy_document_compatibility,
        )

        register_contract_read_providers()
        register_legacy_document_compatibility()
