# backend/apps/common/tests/test_dlq_streams.py
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings

from apps.common.services.dlq_service import AdminDlqManagementService
from apps.common.tasks import BaseEnterpriseTask
from apps.common.tenancy.context import _current_execution_context
from config.celery import app


@app.task(base=BaseEnterpriseTask, name="apps.common.tasks.dlq_fail_stub")
def dlq_fail_stub_task(x):
    raise ValueError("Target system unavailable")


@pytest.mark.django_db
class TestCeleryStreamsDlqMesh:
    def test_task_exhausts_retries_and_can_be_replayed_by_sre(self):
        redis_client = settings.REDIS_CLIENT
        stream_name = "infrastructure:dlq:stream"
        redis_client.delete(stream_name)

        task_uuid = str(uuid.uuid4())
        corr_uuid = uuid.uuid4()

        einfo_mock = MagicMock()
        einfo_mock.traceback = "Traceback lines..."

        dlq_fail_stub_task.push_request(retries=5, headers={"correlation_id": str(corr_uuid)})
        try:
            dlq_fail_stub_task.on_failure(
                exc=ValueError("Target system unavailable"),
                task_id=task_uuid,
                args=[42],
                kwargs={},
                einfo=einfo_mock,
            )
        finally:
            dlq_fail_stub_task.pop_request()

        dlq_records = AdminDlqManagementService.inspect_dead_letters(count=1)
        assert len(dlq_records) == 1
        record = dlq_records[0]
        assert record["task_name"] == "apps.common.tasks.dlq_fail_stub"
        assert record["args"] == [42]
        assert record["exception_class"] == "ValueError"
        assert record["correlation_id"] == str(corr_uuid)

        _current_execution_context.set(
            {
                "user_id": uuid.uuid4(),
                "request_id": uuid.uuid4(),
                "correlation_id": corr_uuid,
            }
        )

        with patch("config.celery.app.send_task") as mock_send_task:
            AdminDlqManagementService.execute_task_replay(record["sequence_id"])
            mock_send_task.assert_called_once_with(
                name="apps.common.tasks.dlq_fail_stub",
                args=[42],
                kwargs={},
                task_id=task_uuid,
                headers={"correlation_id": str(corr_uuid)},
            )

        assert len(AdminDlqManagementService.inspect_dead_letters(count=1)) == 0
