from django.db import transaction, connection
from apps.common.context.core import get_context, is_primary_db_enforced
from apps.common.infra.redis import get_default_redis


class EnterpriseClusterRouter:
    """
    [ФИНАЛЬНЫЙ КАНОН v3.7] Высоконагруженный CQRS Маршрутизатор СУБД.
    [ИСПРАВЛЕНО]: Полностью синхронизирован с apps.common.context.core и ленивой фабрикой Redis.
    Защищает асинхронные потоки Celery/Channels от Transaction Leakage на уровне сессий Postgres.
    """

    def _inject_rls(self) -> None:
        """Инжектирует локальную сессионную переменную в PostgreSQL."""
        try:
            # Извлекаем единый context-паспорт сессии
            ctx = get_context()
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET LOCAL app.current_tenant_id = %s;",
                    [str(ctx.tenant_id)],
                )
        except Exception:
            # Системные кроны Celery Beat и публичный трафик работают вне тенант-контекста
            pass

    def db_for_read(self, model, **hints):
        # Накладываем RLS замок на сессию перед выполнением любого SQL-запроса чтения
        self._inject_rls()

        # Если сработал 2-секундный Redis TTL флага мутации пользователя — форсируем Primary Мастер
        if hints.get("force_primary") or is_primary_db_enforced():
            return "default"

        try:
            # [ИСПРАВЛЕНО]: Вызов через ленивую фабрику. Сравнение идет со строкой (decode_responses=True)
            if get_default_redis().get("infra:postgres_replica:status") == "DEGRADED":
                return "default"
        except Exception:
            # Если Redis временно недоступен — фолбэчимся на безопасное чтение из мастера
            return "default"

        # Защита открытого ACID транзакционного блока Command Side
        if transaction.get_connection("default").in_atomic_block:
            return "default"

        return "postgres_replica"

    def db_for_write(self, model, **hints):
        # Накладываем RLS замок на сессию перед выполнением SQL-запроса записи
        self._inject_rls()
        return "default"

    def allow_relation(self, obj1, obj2, **hints):
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return db == "default"
