# backend/apps/users/apps.py
from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.users"
    label = "users"

    def ready(self):
        from apps.common.contracts.auth_context import register_auth_context_validator
        from apps.users import checks  # noqa: F401
        from apps.users.contracts.auth_context_impl import users_auth_context_validator

        # Middleware authenticates before URL resolution imports config.api.
        register_auth_context_validator(users_auth_context_validator)
