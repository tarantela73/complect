import json
import pytest
import structlog
from io import StringIO


class TestObservabilityLoggingContract:
    def test_logger_produces_valid_json_with_mandatory_fields(self):
        """[LEVEL 2: QUERIES/OBSERVABILITY] Проверяет соответствие JSON-структуры лога ТЗ Комплекта."""
        # 1. Перехватываем вывод логгера в буфер памяти
        log_buffer = StringIO()

        # Настраиваем тестовый логгер через structlog
        test_logger = structlog.get_logger("test_contract")

        # Эмулируем принудительный биндинг контекста SRE-переменных
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id="req-12345",
            correlation_id="corr-67890",
            tenant_id="tenant-alpha",
            user_id="user-chief",
        )

        # [ИСПРАВЛЕНО] Правильный синтаксис MonkeyPatch без двоеточий на методах
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                structlog.PrintLogger,
                "msg",
                lambda self, message: log_buffer.write(message + "\n"),
            )

            # Вызов логирования внутри изолированного контекста патча
            test_logger.info("Бизнес-операция: Закрытие договора")

        log_output = log_buffer.getvalue().strip()

        # 2. Проверяем, что лог — это валидный JSON-объект
        log_json = json.loads(log_output)

        # 3. Проверяем наличие всех обязательных полей по нашему контракту ТЗ [10.2]
        assert "timestamp" in log_json
        assert log_json["level"] == "info"
        assert log_json["request_id"] == "req-12345"
        assert log_json["correlation_id"] == "corr-67890"
        assert log_json["tenant_id"] == "tenant-alpha"
        assert log_json["user_id"] == "user-chief"
        assert log_json["event"] == "Бизнес-операция: Закрытие договора"
