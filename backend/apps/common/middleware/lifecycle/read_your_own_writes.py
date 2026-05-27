import structlog
from django.conf import settings
from apps.common.tenancy.context_core import get_user_id, set_force_primary_db, is_primary_db_enforced

logger = structlog.get_logger(__name__)


class ReadYourOwnWritesStickyMiddleware:
    """
    [ADR 0012 / ИСПРАВЛЕНО] Контроль CQRS Sticky-маршрутизации трафика СУБД.
    [Синхронизировано с context_core.py]: Использует безопасные геттеры акцессоров.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            # Вытягиваем user_id строго через твой новый безопасный акцессор
            user_id = get_user_id()
            redis_client = settings.REDIS_CLIENT
            redis_key = f"sticky:primary:user:{str(user_id)}"

            # Если пользователь совершал изменения менее 2 секунд назад — форсируем Primary Мастер-БД
            if redis_client.exists(redis_key):
                set_force_primary_db(True)
            else:
                # Жесткий сброс флага для текущего потока рантайма, исключающий "вечную" привязку
                set_force_primary_db(False)

        except Exception:
            # Безопасный Fail-Safe сброс для публичного/анонимного трафика
            set_force_primary_db(False)

        response = self.get_response(request)

        # Если запрос совершил успешное изменение состояния, взводим sticky-замок в Redis на 2 секунды
        if request.method in ["POST", "PUT", "PATCH", "DELETE"]:
            if 200 <= response.status_code < 300:
                try:
                    user_id = get_user_id()
                    settings.REDIS_CLIENT.setex(f"sticky:primary:user:{str(user_id)}", 2, "1")
                    set_force_primary_db(True)
                except Exception:
                    pass

        return response
