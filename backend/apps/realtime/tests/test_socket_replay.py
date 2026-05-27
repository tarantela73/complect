import pytest
import json
from channels.testing import WebsocketCommunicator
from config.asgi import application
from django.conf import settings


@pytest.mark.asyncio
class TestWebSocketEventReplayMesh:
    async def test_websocket_negotiates_protocol_and_replays_missed_events(self):
        """[LEVEL 5: WEBSOCKET/REPLAY] Тестирует согласование возможностей и компенсацию пропущенных кадров."""
        redis_client = settings.REDIS_CLIENT
        stream_name = "erp:streams:tenant:global"

        # Очищаем старый стрим
        redis_client.delete(stream_name)

        # 1. Записываем искусственное пропущенное событие в Redis Stream, пока клиент "был оффлайн"
        seq_id = redis_client.xadd(
            stream_name,
            {
                "event_type": "ContractClosedEvent",
                "payload": json.dumps({"contract_id": "test-uuid"}),
            },
        )
        seq_id_str = seq_id.decode("utf-8")

        # 2. Инициализируем тестовый асинхронный сокет-коммуникатор Channels
        communicator = WebsocketCommunicator(application, "/ws/realtime/")
        connected, _ = await communicator.connect()
        assert connected

        # 3. Отправляем connection_init с запросом event_replay [Раздел 8.1 ТЗ]
        # Передаем last_sequence_id на одну позицию раньше (или "0-0" для вычищения всего стрима)
        await communicator.send_json_to(
            {
                "type": "connection_init",
                "protocol_version": 1,
                "capabilities": ["at_least_once", "event_replay"],
                "last_sequence_id": "0-0",  # Запрашиваем историю с самого начала стрима
            }
        )

        # 4. Проверяем получение connection_ack
        ack_response = await communicator.receive_json_from()
        assert ack_response["type"] == "connection_ack"
        assert "event_replay" in ack_response["negotiated_capabilities"]

        # 5. [ГЛАВНАЯ ПРОВЕРКА]: Клиент автоматически получает упущенное событие из Redis Stream!
        event_response = await communicator.receive_json_from()
        assert event_response["type"] == "realtime_event"
        assert event_response["sequence_id"] == seq_id_str
        assert event_response["event_type"] == "ContractClosedEvent"
        assert event_response["is_replay"] is True

        await communicator.disconnect()
