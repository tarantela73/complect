from django.test import Client


class TestCorsSecurityGovernance:
    def test_cors_does_not_return_wildcard_with_credentials(self):
        """[LEVEL 8: SECURITY] Гарантирует отсутствие уязвимости CORS Wildcard + Credentials."""
        client = Client()

        # Эмулируем preflight OPTIONS запрос от браузера с доверенного дев-хоста
        response = client.options(
            "/api/v1/contracts/",
            HTTP_ORIGIN="http://localhost:3000",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )

        # В продакшене это проверяется на уровне Kong, но наш бэкенд-тест
        # страхует конфигурацию и проверяет, что логика не фолбэчится в "*"
        allow_origin = response.headers.get("Access-Control-Allow-Origin")

        # Если заголовок вернулся, он обязан быть конкретным хостом, а не "*"
        if allow_origin:
            assert allow_origin != "*", (
                "CRITICAL SECURITY HOLE: Обнаружен CORS Wildcard '*' при credentials=true!"
            )
            assert allow_origin in ["http://localhost:3000", "https://complect.ru"]
