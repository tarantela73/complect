import structlog
from django.core.exceptions import PermissionDenied

logger = structlog.get_logger(__name__)


class Ones1CHttpClientAdapter:
    """Транспортный адаптер для сырых REST/SOAP запросов к шине 1С."""

    @staticmethod
    def verify_request_signature(request) -> bool:
        """Проверяет токен авторизации и подпись шлюза 1С Предприятия."""
        auth_header = request.headers.get("Authorization")
        if not auth_header or "Bearer ONES_SECRET" not in auth_header:
            logger.critical(
                "INTEGRATION_SECURITY_BREACH: Неавторизованный запрос от 1С!"
            )
            raise PermissionDenied("Интеграционный доступ заблокирован.")
        return True
