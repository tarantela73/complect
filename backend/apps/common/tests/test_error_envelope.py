"""
Contract tests: every HTTP boundary in the system must return rule 6.6 envelope.
Covers Ninja handlers, middleware rejections, Django fallback handlers,
and the outermost ASGI error boundary.
"""

import json
import uuid

import pytest
from django.test import Client, RequestFactory, override_settings

from apps.common.api.errors import (
    django_bad_request,
    django_page_not_found,
    django_permission_denied,
    django_server_error,
    make_error_response,
)
from apps.common.middleware.asgi_error import AsgiErrorBoundaryMiddleware
from apps.users.models import User
from apps.users.services.auth_service import _make_access_token


def _valid_headers() -> dict:
    tenant_id = uuid.uuid4()
    email = f"error_envelope_{uuid.uuid4().hex[:8]}@test.com"
    user = User.objects.create_user(username=email, email=email, password="Pass1!", tenant_id=tenant_id)
    return {
        "HTTP_X_GATEWAY_TOKEN": "complect_internal_secure_mesh_token_2026",
        "HTTP_AUTHORIZATION": f"Bearer {_make_access_token(user)}",
    }


def _assert_envelope(data: dict, expected_code: str) -> None:
    assert "error" in data, f"Missing 'error' key in response: {data}"
    err = data["error"]
    assert err["code"] == expected_code
    assert "message" in err
    assert "detail" in err
    assert "request_id" in err
    assert "correlation_id" in err


# ── make_error_response unit ──────────────────────────────────────────────────


class TestMakeErrorResponse:
    def test_envelope_shape(self):
        result = make_error_response("TEST_CODE", "Test message.")
        _assert_envelope(result, "TEST_CODE")

    def test_detail_defaults_to_none(self):
        result = make_error_response("X", "y")
        assert result["error"]["detail"] is None

    def test_detail_passthrough(self):
        result = make_error_response("X", "y", detail={"field": "err"})
        assert result["error"]["detail"] == {"field": "err"}


# ── Django fallback handlers ──────────────────────────────────────────────────


class TestDjangoFallbackHandlers:
    """
    Django-level handlers (handler400/403/404/500) must return rule 6.6 envelope.
    These fire when errors propagate past Ninja and all middleware.
    """

    def setup_method(self):
        self.rf = RequestFactory()

    def test_bad_request_handler(self):
        request = self.rf.get("/")
        response = django_bad_request(request)
        assert response.status_code == 400
        import json

        data = json.loads(response.content)
        _assert_envelope(data, "BAD_REQUEST")

    def test_permission_denied_handler(self):
        request = self.rf.get("/")
        response = django_permission_denied(request)
        assert response.status_code == 403
        import json

        data = json.loads(response.content)
        _assert_envelope(data, "FORBIDDEN")

    def test_page_not_found_handler(self):
        request = self.rf.get("/")
        response = django_page_not_found(request)
        assert response.status_code == 404
        import json

        data = json.loads(response.content)
        _assert_envelope(data, "NOT_FOUND")

    def test_server_error_handler(self):
        request = self.rf.get("/")
        response = django_server_error(request)
        assert response.status_code == 500
        import json

        data = json.loads(response.content)
        _assert_envelope(data, "INTERNAL_SERVER_ERROR")

    def test_server_error_no_traceback_in_response(self):
        """500 must never leak stack trace or exception message."""
        request = self.rf.get("/")
        response = django_server_error(request)
        import json

        data = json.loads(response.content)
        assert data["error"]["detail"] is None
        assert "traceback" not in str(data)
        assert "exception" not in str(data).lower()


# ── Middleware rejections ─────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
@pytest.mark.urls("config.urls")
class TestMiddlewareEnvelope:
    """Middleware-level rejections must use rule 6.6 envelope."""

    def test_missing_gateway_token_returns_envelope(self):
        client = Client()
        response = client.get("/api/v1/contracts/")
        assert response.status_code in (401, 403)
        _assert_envelope(response.json(), response.json()["error"]["code"])

    def test_missing_tenant_context_returns_auth_gate_denied(self):
        client = Client()
        response = client.get(
            "/api/v1/contracts/",
            HTTP_X_GATEWAY_TOKEN="complect_internal_secure_mesh_token_2026",
        )
        assert response.status_code == 401
        _assert_envelope(response.json(), "AUTH_GATE_DENIED")

    def test_malformed_non_authoritative_tenant_id_cannot_bypass_auth_gate(self):
        client = Client()
        response = client.get(
            "/api/v1/contracts/",
            HTTP_X_TENANT_ID="not-a-uuid",
            HTTP_X_USER_ID="also-not-a-uuid",
            HTTP_X_GATEWAY_TOKEN="complect_internal_secure_mesh_token_2026",
        )
        assert response.status_code == 401
        _assert_envelope(response.json(), "AUTH_GATE_DENIED")

    def test_missing_idempotency_key_returns_envelope(self):
        client = Client()
        response = client.post(
            "/api/v1/contracts/",
            content_type="application/json",
            data="{}",
            **_valid_headers(),
        )
        assert response.status_code == 400
        _assert_envelope(response.json(), "MISSING_IDEMPOTENCY_KEY")

    def test_malformed_idempotency_key_returns_envelope(self):
        client = Client()
        response = client.post(
            "/api/v1/contracts/",
            content_type="application/json",
            data="{}",
            **_valid_headers(),
            HTTP_IDEMPOTENCY_KEY="not-a-uuid",
        )
        assert response.status_code == 400
        _assert_envelope(response.json(), "MALFORMED_IDEMPOTENCY_KEY")


# ── ASGI error boundary ───────────────────────────────────────────────────────


class TestAsgiErrorBoundary:
    """
    AsgiErrorBoundaryMiddleware must catch unhandled exceptions from the
    inner ASGI application and return a rule 6.6 envelope without leaking
    traceback or exception details.
    """

    async def _collect_response(self, scope, receive, send_fn):
        responses = []

        async def capture(event):
            responses.append(event)

        async def boom_app(scope, receive, send):
            raise RuntimeError("simulated ASGI-level crash")

        middleware = AsgiErrorBoundaryMiddleware(boom_app)
        await middleware(scope, receive, capture)
        return responses

    @pytest.mark.asyncio
    async def test_catches_http_exception_returns_500_envelope(self):
        scope = {"type": "http"}
        events = []

        async def capturing_send(event):
            events.append(event)

        async def crashing_app(scope, receive, send):
            raise RuntimeError("ASGI crash before Django")

        middleware = AsgiErrorBoundaryMiddleware(crashing_app)
        await middleware(scope, None, capturing_send)

        start_event = events[0]
        body_event = events[1]

        assert start_event["status"] == 500
        content_type = dict(start_event["headers"])[b"content-type"]
        assert content_type == b"application/json"

        data = json.loads(body_event["body"])
        _assert_envelope(data, "INTERNAL_SERVER_ERROR")
        assert data["error"]["detail"] is None

    @pytest.mark.asyncio
    async def test_websocket_scope_not_intercepted(self):
        """WebSocket failures are outside HTTP contract — must pass through."""
        scope = {"type": "websocket"}
        raised = []

        async def crashing_app(scope, receive, send):
            raised.append(True)
            raise RuntimeError("ws crash")

        middleware = AsgiErrorBoundaryMiddleware(crashing_app)
        with pytest.raises(RuntimeError):
            await middleware(scope, None, None)

        assert raised  # app was called, exception propagated

    @pytest.mark.asyncio
    async def test_healthy_request_passes_through(self):
        """Normal requests must not be intercepted."""
        scope = {"type": "http"}
        called = []

        async def healthy_app(scope, receive, send):
            called.append(True)

        middleware = AsgiErrorBoundaryMiddleware(healthy_app)
        await middleware(scope, None, None)

        assert called


# ── Ninja validation error ────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
@pytest.mark.urls("config.urls")
class TestNinjaValidationEnvelope:
    """Ninja ValidationError must return rule 6.6 envelope."""

    def test_invalid_uuid_path_param_returns_envelope(self):
        client = Client()
        response = client.get(
            "/api/v1/contracts/not-a-valid-uuid/",
            **_valid_headers(),
        )
        # ninja_validation_error_handler maps ValidationError → 400 VALIDATION_ERROR
        assert response.status_code == 400
        _assert_envelope(response.json(), "VALIDATION_ERROR")
