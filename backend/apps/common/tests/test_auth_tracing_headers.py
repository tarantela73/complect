"""Tracing headers cannot turn valid verified authentication into an error."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from django.conf import settings
from django.test import RequestFactory

from apps.common.api import auth
from apps.common.context.core import ContextCore, clear_context
from apps.common.contracts.auth_context import ValidatedAuthContext


@pytest.mark.parametrize("header", ["X-Request-ID", "X-Correlation-ID"])
def test_invalid_tracing_header_preserves_verified_identity(header, monkeypatch):
    clear_context()
    try:
        context = ValidatedAuthContext(uuid.uuid4(), uuid.uuid4())
        monkeypatch.setattr(auth, "get_auth_context_validator",
                            lambda: SimpleNamespace(validate=lambda payload: context))
        token = jwt.encode({"type": "access", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
                           settings.SECRET_KEY, algorithm="HS256")
        request = RequestFactory().get("/", headers={header: "invalid"})
        assert auth.JWTAuth().authenticate(request, token) is not None
        assert auth.verified_request_context(request) == context
        assert isinstance(ContextCore.request_id.get(), uuid.UUID)
        assert isinstance(ContextCore.correlation_id.get(), uuid.UUID)
    finally:
        clear_context()


def test_valid_tracing_headers_are_preserved(monkeypatch):
    clear_context()
    try:
        context = ValidatedAuthContext(uuid.uuid4(), uuid.uuid4())
        monkeypatch.setattr(auth, "get_auth_context_validator",
                            lambda: SimpleNamespace(validate=lambda payload: context))
        token = jwt.encode({"type": "access", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
                           settings.SECRET_KEY, algorithm="HS256")
        request_id, correlation_id = uuid.uuid4(), uuid.uuid4()
        request = RequestFactory().get("/", headers={
            "X-Request-ID": str(request_id), "X-Correlation-ID": str(correlation_id)})
        assert auth.JWTAuth().authenticate(request, token) is not None
        assert ContextCore.request_id.get() == request_id
        assert ContextCore.correlation_id.get() == correlation_id
    finally:
        clear_context()
