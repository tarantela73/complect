from django.db import models


class FeatureFlagType(models.TextChoices):
    TEMPORARY = "temporary", "Временный фича-флаг (Релизы фич)"
    PERMANENT = "permanent", "Постоянный флаг (Бизнес-разветвления)"
    EXPERIMENT = "experiment", "Экспериментальный флаг (A/B тесты)"
    OPS = "ops", "Операционный флаг (Инфраструктура)"
    KILL_SWITCH = "kill-switch", "Аварийный рубильник (SLA/SRE)"


class FeatureFlag(models.Model):
    """
    [ИСПРАВЛЕНО] Декларативная схема Feature Flags ERP КОМПЛЕКТ.
    Каждая запись содержит строгий системный тип и дату создания для линтера.
    """

    name = models.CharField(
        max_length=100, primary_key=True
    )  # Строковое имя-ключ флага
    flag_type = models.CharField(max_length=20, choices=FeatureFlagType.choices)
    is_enabled = models.BooleanField(default=False)
    description = models.TextField(blank=True)

    # Обязательные метаданные для предотвращения Feature-Flag Hell [Раздел 9 ТЗ]
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "common_feature_flags"
