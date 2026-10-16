from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"

    def ready(self) -> None:
        # The table is owned by the already applied common migration; this
        # import only registers its historical model with Django's app
        # registry and creates no schema objects.
        from apps.common.models import (
            legacy_task_recovery,  # noqa: F401
            rbac,  # noqa: F401
        )
        from apps.common.security import audit  # noqa: F401
