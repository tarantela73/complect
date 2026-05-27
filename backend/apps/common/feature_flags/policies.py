import django.utils.timezone as tz
from django.core.exceptions import ValidationError
from apps.common.models.feature_flag import FeatureFlag, FeatureFlagType


class FeatureFlagCleanupPolicy:
    """[9.1 CLEANUP LAW] Проверяет инварианты возраста флагов для защиты от мусора в коде."""

    @staticmethod
    def verify_flag_age(flag: FeatureFlag) -> None:
        """Вызывает исключение, если временный или экспериментальный флаг живет в системе дольше 90 дней."""
        if flag.flag_type in [FeatureFlagType.TEMPORARY, FeatureFlagType.EXPERIMENT]:
            age_days = (tz.now() - flag.created_at).days
            if age_days > 90:
                raise ValidationError(
                    f"CRITICAL CODE DEGRADATION: Фича-флаг '{flag.name}' нарушил Cleanup Law! "
                    f"Его возраст составляет {age_days} дней. Требуется физическое вырезание кода фичи."
                )
