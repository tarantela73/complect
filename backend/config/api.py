from ninja import NinjaAPI
from ninja.errors import AuthenticationError, ValidationError

from apps.common.api.errors import ninja_auth_error_handler, ninja_validation_error_handler

api = NinjaAPI(
    title="ERP Комплект API",
    version="1.0.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

api.add_exception_handler(ValidationError, ninja_validation_error_handler)
api.add_exception_handler(AuthenticationError, ninja_auth_error_handler)
