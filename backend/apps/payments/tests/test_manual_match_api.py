"""HTTP regressions for manual Payments matching concurrency handling."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.db import connections
from django.db import router as django_router
from django.test import Client, override_settings

from apps.common.api import permissions
from apps.common.context.core import ContextCore
from apps.common.contracts.auth_context import ValidatedAuthContext
from apps.common.db.router import EnterpriseClusterRouter
from apps.common.models.outbox import OutboxRecord
from apps.payments import api as payments_api
from apps.payments.models.bank_statement import BankStatement, BankStatementStatus
from apps.payments.models.bank_statement_line import BankStatementLine, MatchStatus


def _fixture(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    context = ValidatedAuthContext(actor_id=actor_id, tenant_id=tenant_id)
    tenant_token = ContextCore.tenant_id.set(tenant_id)
    user_token = ContextCore.user_id.set(actor_id)
    request.addfinalizer(lambda: ContextCore.tenant_id.reset(tenant_token))
    request.addfinalizer(lambda: ContextCore.user_id.reset(user_token))

    # Explicit HTTP auth/RBAC fixture seam. The real Ninja decorator and
    # production exception handlers still execute; no real token or user
    # membership lookup is needed for this isolated concurrency regression.
    def fixture_auth(request):
        request.jwt_tenant_id = tenant_id
        request.jwt_user_id = actor_id
        ContextCore.tenant_id.set(tenant_id)
        ContextCore.user_id.set(actor_id)
        return True

    monkeypatch.setattr(permissions, "jwt_auth", fixture_auth)
    monkeypatch.setattr(permissions, "verified_request_context", lambda request: context)
    monkeypatch.setattr(permissions.PolicyEngine, "check", staticmethod(lambda **kwargs: None))
    return tenant_id, actor_id


def _line(
    tenant_id: uuid.UUID,
    *,
    status: str = MatchStatus.UNMATCHED,
    confidence: int = 0,
    transaction_id: uuid.UUID | None = None,
) -> BankStatementLine:
    statement = BankStatement.objects.create(
        tenant_id=tenant_id,
        file_name="manual-match-http.txt",
        file_format="text",
        raw_text="",
        status=BankStatementStatus.IMPORTED,
    )
    return BankStatementLine.objects.create(
        tenant_id=tenant_id,
        statement=statement,
        line_number=1,
        parse_status="parsed",
        amount=Decimal("5000.00"),
        match_status=status,
        match_confidence=confidence,
        transaction_id=transaction_id,
    )


def _match_url(line: BankStatementLine) -> str:
    return f"/api/v1/payments/statements/{line.statement_id}/lines/{line.id}/match/"


def _finder(monkeypatch: pytest.MonkeyPatch, transaction_id: uuid.UUID) -> None:
    dto = MagicMock()
    dto.transaction_id = transaction_id
    dto.contract_id = uuid.uuid4()
    dto.amount_kopecks = 500000
    dto.external_ref = "HTTP-REPRO"
    dto.counterparty_inn = "7700000001"
    dto.value_date = None
    finder = MagicMock()
    finder.find_by_id.return_value = dto
    monkeypatch.setattr(
        "apps.finance.services.transaction_finder.get_transaction_finder",
        lambda: finder,
    )


def _snapshot(line_id: uuid.UUID) -> dict:
    fields = [field.attname for field in BankStatementLine._meta.concrete_fields]
    snapshot = BankStatementLine.objects.values(*fields).get(pk=line_id)
    return json.loads(json.dumps(snapshot, default=str))


def _assert_only_version_changed(before: dict, after: dict, expected_version: int) -> None:
    assert after["version"] == expected_version
    for field, value in before.items():
        if field != "version":
            assert after[field] == value


def _bump_version_on_committed_writer(
    line: BankStatementLine, tenant_id: uuid.UUID
) -> tuple[int, int]:
    """Create the conflict after the read on a distinct committed connection."""
    import psycopg

    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        primary_pid = int(cursor.fetchone()[0])

    writer_params = connections["default"].get_connection_params()
    with psycopg.connect(**writer_params) as writer:
        with writer.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            writer_pid = int(cursor.fetchone()[0])
            cursor.execute("SET app.current_tenant_id = %s", [str(tenant_id)])
            cursor.execute(
                f'UPDATE "{line._meta.db_table}" SET version = version + 1 '
                "WHERE id = %s AND tenant_id = %s",
                [str(line.id), str(tenant_id)],
            )
            assert cursor.rowcount == 1
    return primary_pid, writer_pid


@pytest.mark.django_db(transaction=True)
@override_settings(
    MIDDLEWARE=[], DEBUG=False, DEBUG_PROPAGATE_EXCEPTIONS=False, ROOT_URLCONF="config.urls"
)
def test_http_attach_stale_version_returns_409_and_preserves_mutation(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    record_property: Callable[[str, object], None],
) -> None:
    tenant_id, _ = _fixture(monkeypatch, request)
    transaction_id = uuid.uuid4()
    line = _line(tenant_id)
    _finder(monkeypatch, transaction_id)
    client = Client(raise_request_exception=False)
    original_get_line = payments_api._get_line

    connection_pids: tuple[int, int] | None = None

    def stale_get_line(stmt_id, line_id, request_tenant):
        nonlocal connection_pids
        value = original_get_line(stmt_id, line_id, request_tenant)
        connection_pids = _bump_version_on_committed_writer(value, tenant_id)
        return value

    monkeypatch.setattr(payments_api, "_get_line", stale_get_line)
    before = _snapshot(line.id)
    outbox_before = OutboxRecord.objects.count()
    response = client.post(
        _match_url(line),
        data=json.dumps({"transaction_id": str(transaction_id)}),
        content_type="application/json",
    )
    after = _snapshot(line.id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONCURRENT_MODIFICATION"
    _assert_only_version_changed(before, after, before["version"] + 1)
    assert OutboxRecord.objects.count() == outbox_before
    assert connection_pids is not None
    primary_pid, writer_pid = connection_pids
    assert primary_pid != writer_pid
    record_property(
        "http_evidence",
        {
            "endpoint": _match_url(line),
            "status": response.status_code,
            "error_code": response.json()["error"]["code"],
            "before": before,
            "after": after,
            "outbox_before": outbox_before,
            "outbox_after": OutboxRecord.objects.count(),
            "primary_pid": str(primary_pid),
            "writer_pid": str(writer_pid),
        },
    )


@pytest.mark.django_db(transaction=True)
@override_settings(
    MIDDLEWARE=[], DEBUG=False, DEBUG_PROPAGATE_EXCEPTIONS=False, ROOT_URLCONF="config.urls"
)
def test_http_detach_stale_version_returns_409_and_preserves_mutation(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    record_property: Callable[[str, object], None],
) -> None:
    tenant_id, _ = _fixture(monkeypatch, request)
    transaction_id = uuid.uuid4()
    line = _line(
        tenant_id,
        status=MatchStatus.MATCHED,
        confidence=100,
        transaction_id=transaction_id,
    )
    client = Client(raise_request_exception=False)
    original_get_line = payments_api._get_line

    connection_pids: tuple[int, int] | None = None

    def stale_get_line(stmt_id, line_id, request_tenant):
        nonlocal connection_pids
        value = original_get_line(stmt_id, line_id, request_tenant)
        connection_pids = _bump_version_on_committed_writer(value, tenant_id)
        return value

    monkeypatch.setattr(payments_api, "_get_line", stale_get_line)
    before = _snapshot(line.id)
    outbox_before = OutboxRecord.objects.count()
    response = client.delete(_match_url(line))
    after = _snapshot(line.id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONCURRENT_MODIFICATION"
    _assert_only_version_changed(before, after, before["version"] + 1)
    assert OutboxRecord.objects.count() == outbox_before
    assert connection_pids is not None
    primary_pid, writer_pid = connection_pids
    assert primary_pid != writer_pid
    record_property(
        "http_evidence",
        {
            "endpoint": _match_url(line),
            "status": response.status_code,
            "error_code": response.json()["error"]["code"],
            "before": before,
            "after": after,
            "outbox_before": outbox_before,
            "outbox_after": OutboxRecord.objects.count(),
            "primary_pid": str(primary_pid),
            "writer_pid": str(writer_pid),
        },
    )


@pytest.mark.django_db(transaction=True)
@override_settings(
    MIDDLEWARE=[], DEBUG=False, DEBUG_PROPAGATE_EXCEPTIONS=False, ROOT_URLCONF="config.urls"
)
def test_http_attach_and_detach_controls_return_200(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    tenant_id, _ = _fixture(monkeypatch, request)
    transaction_id = uuid.uuid4()
    line = _line(tenant_id)
    _finder(monkeypatch, transaction_id)
    client = Client()

    attach = client.post(
        _match_url(line),
        data=json.dumps({"transaction_id": str(transaction_id)}),
        content_type="application/json",
    )
    detach = client.delete(_match_url(line))

    assert attach.status_code == 200
    assert detach.status_code == 200


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("operation", ["attach", "detach"])
@override_settings(
    MIDDLEWARE=[], DEBUG=False, DEBUG_PROPAGATE_EXCEPTIONS=False, ROOT_URLCONF="config.urls"
)
def test_http_command_line_read_is_pinned_to_default(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    operation: str,
    record_property: Callable[[str, object], None],
) -> None:
    tenant_id, _ = _fixture(monkeypatch, request)
    transaction_id = uuid.uuid4()
    line = _line(
        tenant_id,
        status=MatchStatus.MATCHED if operation == "detach" else MatchStatus.UNMATCHED,
        confidence=100 if operation == "detach" else 0,
        transaction_id=transaction_id if operation == "detach" else None,
    )
    if operation == "attach":
        _finder(monkeypatch, transaction_id)

    # Exercise the real EnterpriseClusterRouter with a healthy replica.  The
    # only wrapper records the model's actual selected alias after _get_line;
    # it does not replace the selector or manager.
    original_routers = django_router.routers
    django_router.routers = [EnterpriseClusterRouter()]
    monkeypatch.setattr(
        "apps.common.db.router.get_default_redis",
        lambda: MagicMock(get=lambda key: None),
    )
    force_token = ContextCore.force_primary.set(False)
    request.addfinalizer(lambda: ContextCore.force_primary.reset(force_token))
    selected_aliases: list[str | None] = []
    original_get_line = payments_api._get_line

    def record_get_line(stmt_id, line_id, request_tenant):
        value = original_get_line(stmt_id, line_id, request_tenant)
        selected_aliases.append(value._state.db if value is not None else None)
        return value

    monkeypatch.setattr(payments_api, "_get_line", record_get_line)
    request.addfinalizer(lambda: setattr(django_router, "routers", original_routers))
    client = Client()
    before = _snapshot(line.id)
    outbox_before = OutboxRecord.objects.count()
    if operation == "attach":
        response = client.post(
            _match_url(line),
            data=json.dumps({"transaction_id": str(transaction_id)}),
            content_type="application/json",
        )
    else:
        response = client.delete(_match_url(line))

    assert response.status_code == 200
    assert selected_aliases == ["default"]
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        primary_pid = str(cursor.fetchone()[0])
    record_property(
        "http_evidence",
        {
            "endpoint": _match_url(line),
            "status": response.status_code,
            "error_code": None,
            "before": before,
            "after": _snapshot(line.id),
            "outbox_before": outbox_before,
            "outbox_after": OutboxRecord.objects.count(),
            "primary_pid": primary_pid,
            "writer_pid": None,
        },
    )
