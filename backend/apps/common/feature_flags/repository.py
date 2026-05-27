import structlog
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from apps.common.models.feature_flag import FeatureFlag

logger = structlog.get_logger(__name__)


class FeatureFlagRepository:
    """Контур субмиллисекундного кэширования и чтения состояний флагов из Redis."""

    CACHE_PREFIX = "flags:"

    @classmethod
    def is_flag_active(cls, flag_name: str) -> bool:
        """Проверяет состояние флага. Сначала опрашивает Redis, фолбэчится в СУБД."""
        redis_client = settings.REDIS_CLIENT
        redis_key = f"{cls.CACHE_PREFIX}{flag_name}"

        # 1. Проверяем кэш в оперативной памяти Redis (TTI < 1ms)
        cached_value = redis_client.get(redis_key)
        if cached_value is not None:
            return cached_value == b"1"

        # 2. Если в кэше пусто (Cache Miss) — лениво читаем из PostgreSQL
        try:
            flag = FeatureFlag.objects.get(name=flag_name)
            is_enabled = flag.is_enabled

            # Консервируем значение в Redis на 1 час (3600с) для последующих запросов
            redis_client.setex(redis_key, 3600, "1" if is_enabled else "0")
            return is_enabled

        except ObjectDoesNotExist:
            # SRE-Защита: Если флага нет в БД, по умолчанию фича ВЫКЛЮЧЕНА (Safe Fail)
            logger.error("FEATURE_FLAG_MISSING_IN_DB", flag_name=flag_name)
            redis_client.setex(
                redis_key, 300, "0"
            )  # Кэшируем негативный ответ на 5 минут
            return False

    @classmethod
    def set_flag_state(cls, flag_name: str, enable: bool) -> None:
        """Транзакционно изменяет состояние флага в СУБД и инвалидирует Redis кэш."""
        FeatureFlag.objects.update_or_create(
            name=flag_name, defaults={"is_enabled": enable}
        )
        redis_client = settings.REDIS_CLIENT
        # Моментально выбиваем старый кэш (Cache Invalidation)
        redis_client.delete(f"{cls.CACHE_PREFIX}{flag_name}")
        logger.info(
            "FEATURE_FLAG_STATE_CHANGED", flag_name=flag_name, is_enabled=enable
        )
