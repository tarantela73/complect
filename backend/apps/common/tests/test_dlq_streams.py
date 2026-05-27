import pytest
import json
import uuid
from unittest.mock import patch, MagicMock
from django.conf import settings

# [ИСПРАВЛЕНО] Импортируем корневой инстанс Celery для регистрации тестового таска
from config.celery import app
from apps.common.tasks import BaseEnterpriseTask
from apps.common.services.dlq_service import AdminDlqManagementService
from apps.common.tenancy.context import _current_execution_context


@app.task(base=BaseEnterpriseTask, name="apps.common.tasks.test_dlq_fail")
def test_dlq_fail_task(x):
    raise ValueError("Target system unavailable")


@pytest.mark.django_db
class TestCeleryStreamsDlqMesh:
    def test_task_exhausts_retries_and_can_be_replayed_by_sre(self):
        """[LEVEL 1: UNIT / LEVEL 8: SRE] Проверяет полный цикл эвакуации задачи в Stream DLQ и её Replay."""
        redis_client = settings.REDIS_CLIENT
        stream_name = "infrastructure:dlq:stream"
        redis_client.delete(stream_name)

        task_uuid = str(uuid.uuid4())
        corr_uuid = uuid.uuid4()

        # Имитируем пятый критический сбой выполнения задачи Celery
        einfo_mock = MagicMock()
        einfo_mock.traceback = "Traceback lines..."

        # Настраиваем context-метаданные запроса внутри Celery воркера
        with patch.object(test_dlq_fail_task, "request") as mock_request:
            mock_request.retries = 5
            mock_request.correlation_id = corr_uuid

            test_dlq_fail_task.on_failure(
                exc=ValueError("Target system unavailable"),
                task_id=task_uuid,
                args=[42],
                kwargs={},
                einfo=einfo_mock,
            )

        # Проверяем, что задача законсервирована в Redis Streams DLQ
        dlq_records = AdminDlqManagementService.inspect_dead_letters(count=1)
        assert len(dlq_records) == 1
        record = dlq_records[0]
        assert record["task_name"] == "apps.common.tasks.test_dlq_fail"
        assert record["args"] == [42]
        assert record["exception_class"] == "ValueError"
        assert record["correlation_id"] == str(corr_uuid)

        # Имитируем действия SRE по восстановлению задачи через Admin UI (Replay)
        # Взводим SRE-контекст пользователя
        _current_execution_context.set(
            {
                "user_id": uuid.uuid4(),
                "request_id": uuid.uuid4(),
                "correlation_id": corr_uuid,
            }
        )

        with patch("config.celery.app.send_task") as mock_send_task:
            AdminDlqManagementService.execute_task_replay(record["sequence_id"])

            # Задача должна переотправиться в Celery с сохранением метаданных
            mock_send_task.assert_called_once_with(
                name="apps.common.tasks.test_dlq_fail",
                args=[42],
                kwargs={},
                task_id=task_uuid,
                headers={"correlation_id": str(corr_uuid)},
            )

        # Проверяем, что после успешного Replay лог DLQ-стрима атомарно очистился
        assert len(AdminDlqManagementService.inspect_dead_letters(count=1)) == 0
