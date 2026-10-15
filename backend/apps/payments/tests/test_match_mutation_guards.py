"""Mutation refusals through Payments services on real PostgreSQL."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connections

from apps.common.models.outbox import OutboxRecord
from apps.common.repository.sre_engine import ConcurrentModificationError
from apps.common.tenancy.context import tenant_rls_scope
from apps.finance.tests.real_attach_support import make_matchable_transaction_id
from apps.payments.models.bank_statement_line import BankStatementLine, MatchStatus
from apps.payments.services.manual_match_service import ManualMatchService
from apps.payments.services.matching_service import MatchingService
from apps.payments.tests.test_manual_match_service import _make_line, _make_statement
from apps.payments.tests.test_matching_service import _finder

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("operation", ["attach", "detach"])
def test_manual_match_refuses_persisted_hold_and_preserves_line_and_outbox(operation):
    tenant_id = uuid.uuid4()
    with tenant_rls_scope(tenant_id):
        transaction_id = make_matchable_transaction_id(tenant_id)
        line = _make_line(_make_statement(tenant_id), tenant_id, match_status=MatchStatus.MATCHED,
                          transaction_id=transaction_id)
        # The hold was applied after the caller loaded the line.
        BankStatementLine.objects.filter(pk=line.pk).update(is_legal_hold=True)
        before = BankStatementLine.objects.values().get(pk=line.pk)
        events_before = list(OutboxRecord.objects.values())

        with pytest.raises(PermissionDenied, match="LEGAL_HOLD_ACTIVE"):
            if operation == "attach":
                ManualMatchService.attach(tenant_id, line, transaction_id, uuid.uuid4())
            else:
                ManualMatchService.detach(tenant_id, line, uuid.uuid4())

        assert BankStatementLine.objects.values().get(pk=line.pk) == before
        assert list(OutboxRecord.objects.values()) == events_before


@pytest.mark.parametrize("operation", ["attach", "detach"])
def test_manual_match_refuses_tenant_argument_outside_current_scope(operation):
    tenant_id = uuid.uuid4()
    with tenant_rls_scope(tenant_id):
        transaction_id = make_matchable_transaction_id(tenant_id)
        line = _make_line(
            _make_statement(tenant_id), tenant_id, match_status=MatchStatus.MATCHED,
            match_confidence=100, transaction_id=transaction_id,
        )
        before = BankStatementLine.objects.values().get(pk=line.pk)
        events_before = list(OutboxRecord.objects.values())
    with tenant_rls_scope(uuid.uuid4()), pytest.raises(PermissionDenied):
        if operation == "attach":
            ManualMatchService.attach(tenant_id, line, transaction_id, uuid.uuid4())
        else:
            ManualMatchService.detach(tenant_id, line, uuid.uuid4())
    with tenant_rls_scope(tenant_id):
        assert BankStatementLine.objects.values().get(pk=line.pk) == before
        assert list(OutboxRecord.objects.values()) == events_before


@pytest.mark.django_db(transaction=True)
def test_parallel_attach_with_same_version_commits_only_one_change_and_event():
    tenant_id = uuid.uuid4()
    with tenant_rls_scope(tenant_id):
        transaction_id = make_matchable_transaction_id(tenant_id)
        line = _make_line(_make_statement(tenant_id), tenant_id, match_status=MatchStatus.PARTIAL)
        line_id = line.id
        events_before = OutboxRecord.objects.count()
    ready = Barrier(2, timeout=10)

    def attach():
        try:
            with tenant_rls_scope(tenant_id):
                snapshot = BankStatementLine.objects.get(pk=line_id)
                assert snapshot.version == 1
                ready.wait()
                try:
                    ManualMatchService.attach(tenant_id, snapshot, transaction_id, uuid.uuid4())
                except ConcurrentModificationError:
                    return "refused"
                return "committed"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(attach) for _ in range(2)]
        assert sorted(future.result(timeout=20) for future in futures) == ["committed", "refused"]

    with tenant_rls_scope(tenant_id):
        persisted = BankStatementLine.objects.get(pk=line_id)
        assert persisted.version == 2
        assert persisted.match_status == MatchStatus.MATCHED
        assert persisted.transaction_id == transaction_id
        assert OutboxRecord.objects.count() == events_before + 1


@pytest.mark.parametrize("guard", ["hold", "stale"])
def test_detach_validates_unmatched_snapshot_before_idempotent_return(guard):
    tenant_id = uuid.uuid4()
    with tenant_rls_scope(tenant_id):
        transaction_id = make_matchable_transaction_id(tenant_id)
        line = _make_line(_make_statement(tenant_id), tenant_id, match_status=MatchStatus.UNMATCHED)
        if guard == "hold":
            BankStatementLine.objects.filter(pk=line.pk).update(is_legal_hold=True)
            expected_error = PermissionDenied
        else:
            BankStatementLine.objects.filter(pk=line.pk).update(
                version=2, match_status=MatchStatus.MATCHED, transaction_id=transaction_id
            )
            expected_error = ConcurrentModificationError
        before = BankStatementLine.objects.values().get(pk=line.pk)
        events_before = list(OutboxRecord.objects.values())

        with pytest.raises(expected_error):
            ManualMatchService.detach(tenant_id, line, uuid.uuid4())

        assert BankStatementLine.objects.values().get(pk=line.pk) == before
        assert list(OutboxRecord.objects.values()) == events_before


def test_automatic_match_refuses_hold_and_rolls_back_entire_batch():
    tenant_id = uuid.uuid4()
    with tenant_rls_scope(tenant_id):
        statement = _make_statement(tenant_id)
        # Ordered IDs put the writable line first, so a late refusal tests rollback.
        first = _make_line(statement, tenant_id, match_status=MatchStatus.PARTIAL)
        second = _make_line(statement, tenant_id, match_status=MatchStatus.PARTIAL)
        BankStatementLine.objects.filter(pk=first.pk).update(id=uuid.UUID(int=1))
        BankStatementLine.objects.filter(pk=second.pk).update(id=uuid.UUID(int=2), is_legal_hold=True)
        before = list(statement.lines.order_by("id").values())
        events_before = list(OutboxRecord.objects.values())

        with pytest.raises(PermissionDenied, match="LEGAL_HOLD_ACTIVE"):
            MatchingService(_finder()).match_statement(statement)

        assert list(statement.lines.order_by("id").values()) == before
        assert list(OutboxRecord.objects.values()) == events_before


@pytest.mark.parametrize("operation", ["attach", "detach"])
def test_manual_match_refuses_stale_version_and_preserves_line_and_outbox(operation):
    tenant_id = uuid.uuid4()
    with tenant_rls_scope(tenant_id):
        transaction_id = make_matchable_transaction_id(tenant_id)
        line = _make_line(_make_statement(tenant_id), tenant_id, match_status=MatchStatus.MATCHED,
                          transaction_id=transaction_id)
        BankStatementLine.objects.filter(pk=line.pk).update(version=2, match_confidence=75)
        before = BankStatementLine.objects.values().get(pk=line.pk)
        events_before = list(OutboxRecord.objects.values())

        with pytest.raises(ConcurrentModificationError):
            if operation == "attach":
                ManualMatchService.attach(tenant_id, line, transaction_id, uuid.uuid4())
            else:
                ManualMatchService.detach(tenant_id, line, uuid.uuid4())

        assert BankStatementLine.objects.values().get(pk=line.pk) == before
        assert list(OutboxRecord.objects.values()) == events_before
