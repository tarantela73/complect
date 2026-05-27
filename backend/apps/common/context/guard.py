import uuid
from functools import wraps
from typing import Callable, Any
from django.core.exceptions import PermissionDenied
import structlog
from apps.common.context.core import ContextCore, clear_context

logger = structlog.get_logger(__name__)


class ContextGuard:
    """
    [ФИНАЛЬНЫЙ КАНОН v3.7] Слой рантайм-защиты от перекрестного заражения потоков памяти
    и выполнения кода без авторизованного контекста арендатора.
    """

    # ==========================================
    # ENFORCEMENT DECORATORS (Проактивная Защита)
    # ==========================================

    @staticmethod
    def require_tenant(fn: Callable[..., Any]) -> Callable[..., Any]:
        """
        [ИСПРАВЛЕНО] Декоратор жесткого требования идентификатора тенанта.
        Запрещает выполнение декорируемой функции, если tenant_id пустой.
        """

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if ContextCore.tenant_id.get() is None:
                raise RuntimeError("TENANT_CONTEXT_REQUIRED")
            return fn(*args, **kwargs)

        return wrapper

    @staticmethod
    def require_full_context(fn: Callable[..., Any]) -> Callable[..., Any]:
        """
        [ИСПРАВЛЕНО] Декоратор тотального контроля паспорта запроса.
        Блокирует вызов, если отсутствует хотя бы один критический метатег сессии.
        """

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if None in [
                ContextCore.tenant_id.get(),
                ContextCore.user_id.get(),
                ContextCore.request_id.get(),
            ]:
                raise RuntimeError("INCOMPLETE_CONTEXT")
            return fn(*args, **kwargs)

        return wrapper

    # ==========================================
    # RUNTIME SRE CHECKS (Реактивная Защита)
    # ==========================================

    @staticmethod
    def assert_clean() -> None:
        """
        [HARD LEAK CHECK] Гарантирует, что поток воркера/корутины стерилен перед стартом.
        Если обнаружен 'призрак' старого тенанта — мгновенно взрывает выполнение (Fail-Fast).
        """
        t_id = ContextCore.tenant_id.get()
        u_id = ContextCore.user_id.get()

        if t_id is not None or u_id is not None:
            logger.critical("CRITICAL_CONTEXT_LEAK_DETECTED", stale_tenant_id=str(t_id), stale_user_id=str(u_id))
            # Жесткий ИБ-стоп: СУБД защищена от несанкционированного доступа
            raise PermissionDenied("CRITICAL_SECURITY_VIOLATION: Context leak detected in runtime thread pool.")

    @staticmethod
    def validate_consistency() -> None:
        """[VALIDATION] Проверяет логическую целостность и типы данных паспорта сессии."""
        t_id = ContextCore.tenant_id.get()
        u_id = ContextCore.user_id.get()
        req_id = ContextCore.request_id.get()
        corr_id = ContextCore.correlation_id.get()

        # Проверка strict-типизации UUID на уровне рантайма
        if not isinstance(t_id, uuid.UUID) or not isinstance(u_id, uuid.UUID):
            raise PermissionDenied("CRITICAL_SECURITY_VIOLATION: Invalid types in security boundary context.")

        # Выявление подозрительных паттернов мисраутинга шлюза Kong
        if req_id == corr_id:
            logger.warning(
                "SUSPICIOUS_CONTEXT_SHAPE",
                request_id=str(req_id),
                message="Request ID and Correlation ID are identical. Potential gateway configuration drift.",
            )

    @staticmethod
    def hard_reset() -> None:
        """[ALWAYS CLEAN] Принудительное тотальное выжигание памяти потока."""
        clear_context()
