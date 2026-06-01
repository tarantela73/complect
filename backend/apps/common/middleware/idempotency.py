import hashlib
import json
import uuid

import structlog
from django.conf import settings
from django.http import JsonResponse

from apps.common.context.core import get_tenant_id
from apps.common.infra.redis import get_idempotency_redis

logger = structlog.get_logger(__name__)


class IdempotencyEnforcementMiddleware:
    """
    [ФИНАЛЬНЫЙ КАНОН v3.7] Контур защиты от повторной обработки мутирующих запросов.
    [ИСПРАВЛЕНО]: Полностью переведен на context_core.py, ленивую фабрику get_idempotency_redis()
    и динамическое вычитывание TTL-таймаутов из конфигурации settings.
    """

    MUTATION_METHODS = {
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in self.MUTATION_METHODS:
            return self.get_response(request)

        idem_key = request.headers.get("Idempotency-Key")

        if not idem_key:
            return JsonResponse(
                {
                    "code": "MISSING_IDEMPOTENCY_KEY",
                    "message": "Для выполнения мутационных операций обязателен заголовок Idempotency-Key.",
                },
                status=400,
            )

        try:
            uuid.UUID(idem_key)
        except ValueError:
            return JsonResponse(
                {
                    "code": "MALFORMED_IDEMPOTENCY_KEY",
                },
                status=400,
            )

        # [ИСПРАВЛЕНО]: Безопасный вызов канонического геттера тенанта из нового ядра
        try:
            tenant_id = str(get_tenant_id())
        except Exception:
            tenant_id = "anonymous"

        # [ИСПРАВЛЕНО]: Использование ленивой фабрики пулов. Строки возвращаются автоматически (без bytes)
        redis_client = get_idempotency_redis()

        body_bytes = request.body or b""
        payload_hash = hashlib.sha256(body_bytes).hexdigest()
        redis_key = f"idempotency:{tenant_id}:{idem_key}"

        cached_data = redis_client.get(redis_key)

        if cached_data:
            # Драйвер Redis с decode_responses=True отдает str, loads отрабатывает напрямую без .decode()
            cached_payload = json.loads(cached_data)

            if cached_payload["payload_hash"] != payload_hash:
                return JsonResponse(
                    {
                        "code": "IDEMPOTENCY_PAYLOAD_MISMATCH",
                        "message": "Конфликт: Idempotency-Key совпадает, но тело запроса изменилось.",
                    },
                    status=409,
                )

            if cached_payload["status"] == "PROCESSING":
                response = JsonResponse(
                    {
                        "code": "REQUEST_IN_PROCESSING",
                        "message": "Предыдущий запрос с данным ключом еще обрабатывается системой. Ожидайте.",
                    },
                    status=425,
                )
                response["Retry-After"] = "3"
                return response

            return JsonResponse(
                cached_payload["response_body"],
                status=cached_payload["status_code"],
                safe=False,
            )

        processing_payload = {
            "status": "PROCESSING",
            "payload_hash": payload_hash,
            "status_code": None,
            "response_body": None,
        }

        # [ИСПРАВЛЕНО]: Динамический лимит времени блокировки PROCESSING из настроек local.py
        acquired = redis_client.set(
            redis_key,
            json.dumps(processing_payload),
            ex=settings.IDEMPOTENCY_PROCESSING_TTL_SECONDS,
            nx=True,
        )

        if not acquired:
            response = JsonResponse(
                {
                    "code": "CONCURRENT_IDEMPOTENT_REQUEST",
                },
                status=425,
            )
            response["Retry-After"] = "3"
            return response

        try:
            response = self.get_response(request)

            if 200 <= response.status_code < 500:
                try:
                    response_body = json.loads(response.content.decode("utf-8"))
                except Exception:
                    response_body = {"detail": "non-json-response"}

                completed_payload = {
                    "status": "COMPLETED",
                    "payload_hash": payload_hash,
                    "status_code": response.status_code,
                    "response_body": response_body,
                }

                # [ИСПРАВЛЕНО]: Динамический лимит консервации ответа CACHE из настроек local.py (24 часа)
                redis_client.set(
                    redis_key,
                    json.dumps(completed_payload),
                    ex=settings.IDEMPOTENCY_TTL_SECONDS,
                )

                logger.info(
                    "idempotency_request_completed",
                    tenant_id=tenant_id,
                    idem_key=idem_key,
                    status_code=response.status_code,
                )

            else:
                # 5xx ошибки не кешируем — атомарно стираем замок, давая клиенту шанс на повтор
                redis_client.delete(redis_key)

                logger.warning(
                    "idempotency_request_failed",
                    tenant_id=tenant_id,
                    idem_key=idem_key,
                    status_code=response.status_code,
                )

            return response

        except Exception as exc:
            # Если выполнение упало — обязательно снимаем lock
            redis_client.delete(redis_key)

            logger.exception(
                "idempotency_unhandled_exception",
                tenant_id=tenant_id,
                idem_key=idem_key,
                error=str(exc),
            )
            raise
