import hashlib
import json
import uuid
import time
from django.conf import settings
from django.http import JsonResponse
from apps.common.tenancy.context import get_current_tenant_id


class IdempotencyEnforcementMiddleware:
    TTL = 86400
    PROCESSING_TTL = 300  # FIX: 5 min safe window (instead of 60s)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):

        if request.method not in ["POST", "PUT", "PATCH", "DELETE"]:
            return self.get_response(request)

        idem_key = request.headers.get("Idempotency-Key")
        if not idem_key:
            return JsonResponse({"code": "MISSING_IDEMPOTENCY_KEY"}, status=400)

        try:
            uuid.UUID(idem_key)
        except ValueError:
            return JsonResponse({"code": "MALFORMED_IDEMPOTENCY_KEY"}, status=400)

        try:
            tenant_id = str(get_current_tenant_id())
        except Exception:
            tenant_id = "anonymous"

        redis = settings.REDIS_CLIENT
        lock_key = f"idempotency:{tenant_id}:{idem_key}"

        body_hash = hashlib.sha256(request.body or b"").hexdigest()

        # -------------------------
        # 1. ATOMIC LOCK
        # -------------------------
        lock_payload = json.dumps(
            {
                "status": "PROCESSING",
                "payload_hash": body_hash,
                "ts": time.time(),
            }
        )

        acquired = redis.set(lock_key, lock_payload, nx=True, ex=self.PROCESSING_TTL)

        if not acquired:
            cached_raw = redis.get(lock_key)

            if not cached_raw:
                return JsonResponse({"code": "CONCURRENT_IDEMPOTENT_REQUEST"}, status=425)

            cached = json.loads(cached_raw)

            # FIX: stale PROCESSING protection
            if cached.get("status") == "PROCESSING":
                if time.time() - cached.get("ts", 0) > self.PROCESSING_TTL:
                    # stale lock recovery
                    redis.delete(lock_key)
                    return self.__call__(request)

                return JsonResponse({"code": "REQUEST_IN_PROCESSING"}, status=425)

            if cached.get("payload_hash") != body_hash:
                return JsonResponse({"code": "IDEMPOTENCY_PAYLOAD_MISMATCH"}, status=409)

            return JsonResponse(
                cached.get("response_body"),
                status=cached.get("status_code", 200),
            )

        # -------------------------
        # 2. EXECUTE
        # -------------------------
        response = self.get_response(request)

        # -------------------------
        # 3. FINALIZE (SAFE WRITE)
        # -------------------------
        try:
            if 200 <= response.status_code < 500:
                try:
                    body = json.loads(getattr(response, "content", b"{}"))
                except Exception:
                    body = {"message": "ok"}

                final_payload = {
                    "status": "COMPLETED",
                    "payload_hash": body_hash,
                    "status_code": response.status_code,
                    "response_body": body,
                }

                # FIX: overwrite-safe (no accidental PROCESSING overwrite)
                redis.set(lock_key, json.dumps(final_payload), ex=self.TTL)

            else:
                redis.delete(lock_key)

        except Exception:
            pass

        return response
