import json

import pytest
from django.test import Client

from apps.common.feature_flags.repository import FeatureFlagRepository
from apps.common.models.feature_flag import FeatureFlag, FeatureFlagType


@pytest.mark.django_db
class TestSreKillSwitchMiddlewareMesh:
    def test_global_middleware_intercepts_unwrapped_endpoints_instantly(self):
        """[LEVEL 8: SRE/INFRASTRUCTURE] Гарантирует сквозной перехват трафика к домену без ручных декораторов."""
        client = Client()
        flag_name = "kill_switch_heavy_analytics"

        # 1. Создаем рубильник в СУБД и принудительно гасим его в Redis (Имитируем аварию / лаг реплики)
        FeatureFlag.objects.create(
            name=flag_name, flag_type=FeatureFlagType.KILL_SWITCH, is_enabled=False
        )
        FeatureFlagRepository.set_flag_state(flag_name, enable=False)

        # 2. Стучимся на ЛЮБУЮ ручку внутри заблокированного домена (даже на плоский незадекорированный стаб)
        # Путь /api/v1/analytics/ перекрывается маппингом нашего Middleware
        response = client.get("/api/v1/analytics/any_unprotected_endpoint_stub")

        # [ГЛАВНАЯ ПРОВЕРКА]: Наше глобальное Middleware сработало как броня!
        # Доступ закрыт, возвращен статус 503 Service Unavailable
        assert response.status_code == 503
        response_json = json.loads(response.content.decode("utf-8"))
        assert response_json["code"] == "DOMAIN_TEMPORARILY_DISABLED"
        assert (
            "отключен автоматической системой надежности SRE"
            in response_json["message"]
        )

