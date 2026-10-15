"""Manual attach through the real Finance provider and persisted Outbox."""

import uuid

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection

from apps.common.models.outbox import OutboxRecord
from apps.common.tenancy.context import tenant_rls_scope
from apps.finance.tests.real_attach_support import make_matchable_transaction_id
from apps.payments.events import BankStatementLineMatchedEvent
from apps.payments.models.bank_statement_line import BankStatementLine, MatchStatus
from apps.payments.services.manual_match_service import ManualMatchService
from apps.payments.tests.test_manual_match_service import _make_line, _make_statement

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def real_attach_scenario():
    rows = []
    for _ in range(2):
        tenant_id = uuid.uuid4()
        with tenant_rls_scope(tenant_id):
            transaction_id = make_matchable_transaction_id(tenant_id)
            line = _make_line(
                _make_statement(tenant_id), tenant_id, match_status=MatchStatus.PARTIAL,
            )
            rows.append((tenant_id, transaction_id, line))
    return rows


def _persisted_lines(rows):
    result = []
    for tenant_id, _, line in rows:
        with tenant_rls_scope(tenant_id):
            result.append(BankStatementLine.objects.values().get(id=line.id))
    return result


def _persisted_outbox():
    return list(OutboxRecord.objects.order_by("id").values())


def test_real_attach_commits_line_and_outbox(real_attach_scenario):
    rows = real_attach_scenario
    before_lines = _persisted_lines(rows)
    before_events = _persisted_outbox()
    tenant_id, transaction_id, line = rows[0]
    actor_id = uuid.uuid4()
    with tenant_rls_scope(tenant_id):
        ManualMatchService.attach(tenant_id, line, transaction_id, actor_id)
    assert not connection.in_atomic_block
    after_lines = _persisted_lines(rows)
    assert after_lines[0]["updated_at"] > before_lines[0]["updated_at"]
    assert after_lines == [
        {
            **before_lines[0],
            "match_status": MatchStatus.MATCHED,
            "match_confidence": 100,
            "transaction_id": transaction_id,
            "version": before_lines[0]["version"] + 1,
            "updated_at": after_lines[0]["updated_at"],
        },
        before_lines[1],
    ]
    events = _persisted_outbox()
    assert len(events) == len(before_events) + 1
    event = next(event for event in events if event["id"] not in {e["id"] for e in before_events})
    assert event["event_type"] == BankStatementLineMatchedEvent.EVENT_TYPE
    payload = event["payload_json"]
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["line_id"] == str(line.id)
    assert payload["statement_id"] == str(line.statement_id)
    assert payload["transaction_id"] == str(transaction_id)
    assert payload["confirmed_by"] == str(actor_id)
    assert payload["previous_status"] == MatchStatus.PARTIAL
    assert payload["match_status"] == MatchStatus.MATCHED
    assert payload["match_confidence"] == 100


@pytest.mark.parametrize("case", ["foreign_transaction", "foreign_line", "missing_transaction", "missing_line"])
def test_real_attach_refusal_preserves_lines_and_outbox(real_attach_scenario, case):
    rows = real_attach_scenario
    before_lines = _persisted_lines(rows)
    before_events = _persisted_outbox()
    tenant_id, transaction_id, line = rows[0]
    if case == "foreign_transaction":
        transaction_id = rows[1][1]
    elif case == "foreign_line":
        line = rows[1][2]
    elif case == "missing_transaction":
        transaction_id = uuid.uuid4()
    else:
        line = BankStatementLine(id=uuid.uuid4(), tenant_id=tenant_id, statement_id=line.statement_id)
    refused = False
    with tenant_rls_scope(tenant_id):
        try:
            ManualMatchService.attach(tenant_id, line, transaction_id, uuid.uuid4())
        except (PermissionDenied, BankStatementLine.DoesNotExist):
            refused = True
    assert not connection.in_atomic_block
    assert _persisted_lines(rows) == before_lines
    assert _persisted_outbox() == before_events
    assert refused


def test_real_attach_outbox_failure_rolls_back_line(real_attach_scenario, monkeypatch):
    rows = real_attach_scenario
    before_lines = _persisted_lines(rows)
    before_events = _persisted_outbox()
    tenant_id, transaction_id, line = rows[0]

    def refuse_insert(*args, **kwargs):
        persisted = BankStatementLine.objects.get(id=line.id)
        assert persisted.transaction_id == transaction_id
        assert persisted.match_status == MatchStatus.MATCHED
        raise RuntimeError("injected outbox failure")

    monkeypatch.setattr(OutboxRecord.objects, "create", refuse_insert)
    with tenant_rls_scope(tenant_id), pytest.raises(RuntimeError, match="injected outbox failure"):
        ManualMatchService.attach(tenant_id, line, transaction_id, uuid.uuid4())
    assert not connection.in_atomic_block
    assert _persisted_lines(rows) == before_lines
    assert _persisted_outbox() == before_events


def test_real_attach_repeat_preserves_success_contract(real_attach_scenario):
    rows = real_attach_scenario
    tenant_id, transaction_id, line = rows[0]
    actor_id = uuid.uuid4()
    with tenant_rls_scope(tenant_id):
        ManualMatchService.attach(tenant_id, line, transaction_id, actor_id)
    after_first = _persisted_lines(rows)
    first_events = _persisted_outbox()
    with tenant_rls_scope(tenant_id):
        ManualMatchService.attach(tenant_id, line, transaction_id, actor_id)
    assert not connection.in_atomic_block
    assert _persisted_lines(rows) == after_first
    events = _persisted_outbox()
    assert len(first_events) == 1
    assert len(events) == 2
    repeated = next(event for event in events if event["id"] != first_events[0]["id"])
    payload = repeated["payload_json"]
    assert repeated["event_type"] == BankStatementLineMatchedEvent.EVENT_TYPE
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["line_id"] == str(line.id)
    assert payload["transaction_id"] == str(transaction_id)
    assert payload["confirmed_by"] == str(actor_id)
    assert payload["previous_status"] == MatchStatus.MATCHED
    assert payload["match_status"] == MatchStatus.MATCHED
    assert payload["match_confidence"] == 100
