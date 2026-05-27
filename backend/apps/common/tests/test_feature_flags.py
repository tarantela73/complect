import pytest
import json
from django.test import Client
from apps.common.models.feature_flag import FeatureFlag, FeatureFlagType
from apps.common.feature_flags.repository import FeatureFlagRepository


@pytest.mark.django_db
class TestFeatureFlagsGovernanceMesh:
    def test_kill_switch_flag_instantly_blocks_api_endpoint(self):
        """[LEVEL 8: SRE/SECURITY] Проверяет мгновенную изоляцию эндпоинта при активации рубильника."""
        client = Client()
        flag_name = "kill_switch_heavy_analytics"

        # 1. Создаем аварийный рубильник в базе данных и включаем его по умолчанию
        FeatureFlag.objects.create(
            name=flag_name, flag_type=FeatureFlagType.KILL_SWITCH, is_enabled=True
        )

        # Сбрасываем кэш, чтобы репозиторий прочитал свежие данные
        FeatureFlagRepository.set_flag_state(flag_name, enable=True)
        assert FeatureFlagRepository.is_flag_active(flag_name) is True

        # 2. Имитируем падение SLA: SRE-команда или Prometheus выключает флаг в Redis
        FeatureFlagRepository.set_flag_state(flag_name, enable=False)
        assert FeatureFlagRepository.is_flag_active(flag_name) is False

        # 3. Делаем HTTP-запрос к ручке, защищенной декоратором enforce_feature_gate
        # (Предполагается, что тестовая ручка /api/v1/analytics/ обернута в декоратор)
        response = client.get("/api/v1/analytics/dashboard_stub")

        # [ГЛАВНАЯ ПРОВЕРКА]: Трафик моментально отбит шлюзом бэкенда без нагрузки на СУБД!
        assert response.status_code == 503
        response_json = json.loads(response.content.decode("utf-8"))
        assert response_json["code"] == "FEATURE_DISABLED_BY_GOVERNANCE"
