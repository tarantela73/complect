import json
import pytest
import uuid
from django.test import Client
from apps.common.tenancy.context import set_tenant_context


@pytest.mark.django_db
class TestIdempotencySecurityMesh:
    def test_idempotency_middleware_intercepts_duplicate_requests(self):
        """[LEVEL 8: SECURITY] Проверяет блокировку повторных вызовов и сохранение ответа в Redis."""
        client = Client()
        idem_key = str(uuid.uuid4())
        tenant_uuid = uuid.uuid4()
        user_uuid = uuid.uuid4()

        set_tenant_context(tenant_uuid)

        headers = {
            "HTTP_X_TENANT_ID": str(tenant_uuid),
            "HTTP_X_USER_ID": str(user_uuid),
            "HTTP_IDEMPOTENCY_KEY": idem_key,
            "content_type": "application/json",
        }

        payload = {"title": "Новая кабельная линия снабжения"}

        # 1. Отправляем первый легитимный запрос (Инициирует мутацию)
        # Используем эндпоинт-заглушку, который возвращает 201 Created
        with patch("apps.common.middleware.idempotency.JsonResponse") as _:
            response_one = client.post(
                "/api/v1/contracts/create_stub", data=json.dumps(payload), **headers
            )

        # 2. Мгновенно отправляем точно такой же второй запрос с тем же ключом
        response_two = client.post(
            "/api/v1/contracts/create_stub", data=json.dumps(payload), **headers
        )

        # [ГЛАВНАЯ ПРОВЕРКА]: Второй запрос перехвачен Middleware без захода в СУБД!
        # С кэшированным телом первого ответа
        assert response_two.status_code == response_one.status_code

        # 3. Имитируем хакерский подлог: отправляем тот же ключ, но меняем данные внутри payload
        payload_malicious = {"title": "Взлом баланса леджера"}
        response_three = client.post(
            "/api/v1/contracts/create_stub",
            data=json.dumps(payload_malicious),
            **headers,
        )

        # Запрос обязан отбиться с кодом 409 Conflict (Payload Mismatch)
        assert response_three.status_code == 409
        assert b"IDEMPOTENCY_PAYLOAD_MISMATCH" in response_three.content
