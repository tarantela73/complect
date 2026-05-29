import json
from io import StringIO

import structlog


class TestObservabilityLoggingContract:
    def test_logger_produces_valid_json_with_mandatory_fields(self):
        log_buffer = StringIO()

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id="req-12345",
            correlation_id="corr-67890",
            tenant_id="tenant-alpha",
            user_id="user-chief",
        )

        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.TimeStamper(fmt="iso", key="timestamp"),
                structlog.processors.add_log_level,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.BoundLogger,
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(file=log_buffer),
        )

        test_logger = structlog.get_logger("test_contract")
        test_logger.info("Бизнес-операция: Закрытие договора")

        log_output = log_buffer.getvalue().strip()
        log_json = json.loads(log_output)

        assert "timestamp" in log_json
        assert log_json["level"] == "info"
        assert log_json["request_id"] == "req-12345"
        assert log_json["correlation_id"] == "corr-67890"
        assert log_json["tenant_id"] == "tenant-alpha"
        assert log_json["user_id"] == "user-chief"
        assert log_json["event"] == "Бизнес-операция: Закрытие договора"
