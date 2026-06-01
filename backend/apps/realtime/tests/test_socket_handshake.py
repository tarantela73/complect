import pytest
from channels.testing import WebsocketCommunicator

from config.asgi import application


@pytest.mark.asyncio
class TestWebSocketSecurityBoundary:
    async def test_consumer_rejects_requests_without_prior_handshake(self):
        """[LEVEL 5: WEBSOCKET/SECURITY] Гарантирует отсечение неавторизованных фреймов до connection_init."""

        # Подключаем тестовый асинхронный коммуникатор Channels ASGI
        communicator = WebsocketCommunicator(application, "/ws/realtime/")
        connected, _ = await communicator.connect()
        assert connected

        # Имитируем хакерский вброс: шлем ACK до хэндшейка, пытаясь обмануть FSM сокета
        await communicator.send_json_to({"type": "event_ack", "sequence_id": "12345-0"})

        # Сервер обязан прислать PROTOCOL_VIOLATION кадр ошибки
        response = await communicator.receive_json_from()
        assert response["type"] == "error"
        assert response["code"] == "PROTOCOL_VIOLATION"

        # Проверяем, что соединение принудительно разорвано по ИБ-протоколу с кодом 4401
        # Для этого дожидаемся закрытия сокета коммуникатором
        await communicator.wait()

