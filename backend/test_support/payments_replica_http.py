"""Runtime-only HTTP actions for the physical Payments replica gate.

The host controls PostgreSQL replay with an owner connection.  This module is
run as the restricted application principal and never receives that secret.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import django


def _setup() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    django.setup()


def _snapshot(line_id: uuid.UUID, *, using: str, tenant_id: uuid.UUID | None = None) -> dict[str, Any] | None:
    from apps.payments.models.bank_statement_line import BankStatementLine


    def query() -> dict[str, Any] | None:
        return BankStatementLine.objects.using(using).filter(pk=line_id).values(
            "id", "version", "match_status", "match_confidence", "transaction_id", "description"
        ).first()

    if tenant_id is None:
        return query()
    from apps.common.tenancy.context import tenant_rls_scope

    with tenant_rls_scope(tenant_id, using=using):
        return query()


def _write(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")
    return value


def _ids(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


@contextmanager
def _tenant_scope(tenant_id: uuid.UUID):
    from apps.common.tenancy.context import tenant_rls_scope

    with tenant_rls_scope(tenant_id):
        yield


def prepare(output: Path) -> dict[str, Any]:
    from decimal import Decimal

    from apps.payments.models.bank_statement import BankStatement, BankStatementStatus
    from apps.payments.models.bank_statement_line import BankStatementLine, MatchStatus

    tenant_id, actor_id, transaction_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with _tenant_scope(tenant_id):
        statement = BankStatement.objects.using("default").create(
            tenant_id=tenant_id, file_name="replica-gate.txt", file_format="text",
            raw_text="", status=BankStatementStatus.IMPORTED,
        )
        line = BankStatementLine.objects.using("default").create(
            tenant_id=tenant_id, statement=statement, line_number=1,
            parse_status="parsed", amount=Decimal("5000.00"), match_status=MatchStatus.UNMATCHED,
            match_confidence=0, description="replica-gate-before",
        )
    return _write(output, {
        "tenant_id": str(tenant_id), "actor_id": str(actor_id),
        "transaction_id": str(transaction_id), "statement_id": str(statement.id),
        "line_id": str(line.id), "primary_alias": "default", "replica_alias": "replica",
        "primary_before": _snapshot(line.id, using="default", tenant_id=tenant_id),
    })


def lag_write(ids_path: Path, output: Path) -> dict[str, Any]:
    from django.db.models import F

    from apps.payments.models.bank_statement_line import BankStatementLine


    payload = _ids(ids_path)
    tenant_id = uuid.UUID(payload["tenant_id"])
    line_id = uuid.UUID(payload["line_id"])
    with _tenant_scope(tenant_id):
        updated = BankStatementLine.objects.using("default").filter(pk=line_id).update(
            description="replica-gate-after-pause", version=F("version") + 1
        )
    if updated != 1:
        raise AssertionError("primary lag-write did not update exactly one line")
    return _write(output, {
        "action": "lag-write", "line_id": str(line_id),
        "primary_after": _snapshot(line_id, using="default", tenant_id=tenant_id),
        "replica_while_paused": _snapshot(line_id, using="replica", tenant_id=tenant_id),
    })


def observe(ids_path: Path, output: Path) -> dict[str, Any]:
    payload = _ids(ids_path)
    tenant_id = uuid.UUID(payload["tenant_id"])
    line_id = uuid.UUID(payload["line_id"])
    with _tenant_scope(tenant_id):
        result = {
            "action": "observe", "line_id": str(line_id),
            "primary": _snapshot(line_id, using="default", tenant_id=tenant_id),
            "replica": _snapshot(line_id, using="replica", tenant_id=tenant_id),
        }
    if result["primary"] is None or result["replica"] is None:
        raise AssertionError("line is missing from primary or replica snapshot")
    if result["primary"]["version"] == result["replica"]["version"]:
        raise AssertionError("replica snapshot is not stale while replay is paused")
    return _write(output, result)



def recovered(ids_path: Path, output: Path) -> dict[str, Any]:
    payload = _ids(ids_path)
    tenant_id = uuid.UUID(payload["tenant_id"])
    line_id = uuid.UUID(payload["line_id"])
    with _tenant_scope(tenant_id):
        result = {"action": "recovered", "primary": _snapshot(line_id, using="default", tenant_id=tenant_id),
                  "replica": _snapshot(line_id, using="replica", tenant_id=tenant_id)}
    if result["primary"] != result["replica"]:
        raise AssertionError("replica has not recovered the primary state")
    commands_output = output.with_name(f"{output.stem}-commands.json")
    result["commands"] = commands(ids_path, commands_output)["commands"]
    return _write(output, result)


def commands(ids_path: Path, output: Path) -> dict[str, Any]:
    """Run attach/detach through the real HTTP route after replica recovery."""
    from unittest.mock import MagicMock, patch

    from django.db import connections
    from django.db import router as django_router
    from django.test import Client, override_settings

    from apps.common.api import permissions
    from apps.common.context.core import ContextCore
    from apps.common.contracts.auth_context import ValidatedAuthContext
    from apps.common.db.router import EnterpriseClusterRouter
    from apps.common.models.outbox import OutboxRecord
    from apps.common.tenancy.context import tenant_rls_scope
    from apps.payments import api as payments_api
    payload = _ids(ids_path)
    tenant_id = uuid.UUID(payload["tenant_id"])
    actor_id = uuid.UUID(payload["actor_id"])
    line_id = uuid.UUID(payload["line_id"])
    transaction_id = uuid.UUID(payload["transaction_id"])
    context = ValidatedAuthContext(actor_id=actor_id, tenant_id=tenant_id)

    def auth(request: Any) -> bool:
        request.jwt_tenant_id = tenant_id
        request.jwt_user_id = actor_id
        ContextCore.tenant_id.set(tenant_id)
        ContextCore.user_id.set(actor_id)
        return True

    finder = MagicMock()
    finder.find_by_id.return_value = MagicMock(
        transaction_id=transaction_id,
        contract_id=uuid.uuid4(),
        amount_kopecks=500000,
        external_ref="REPLICA-GATE",
        counterparty_inn="7700000001",
        value_date=None,
    )
    original_routers = django_router.routers
    selected_aliases: list[str | None] = []
    original_get_line = payments_api._get_line

    def record_get_line(stmt_id: Any, line_id_arg: Any, request_tenant: Any) -> Any:
        value = original_get_line(stmt_id, line_id_arg, request_tenant)
        selected_aliases.append(value._state.db if value is not None else None)
        return value

    token_tenant = ContextCore.tenant_id.set(tenant_id)
    token_actor = ContextCore.user_id.set(actor_id)
    force_token = ContextCore.force_primary.set(False)
    try:
        with (
            override_settings(
                MIDDLEWARE=[],
                DEBUG=False,
                DEBUG_PROPAGATE_EXCEPTIONS=False,
                ROOT_URLCONF="config.urls",
            ),
            patch.object(permissions, "jwt_auth", auth),
            patch.object(permissions, "verified_request_context", lambda request: context),
            patch.object(permissions.PolicyEngine, "check", staticmethod(lambda **kwargs: None)),
            patch(
                "apps.finance.services.transaction_finder.get_transaction_finder",
                lambda: finder,
            ),
            patch(
                "apps.common.tasks.outbox_dispatcher.run_outbox_polling_dispatcher_task.apply_async",
                return_value=None,
            ),
            patch(
                "apps.common.db.router.get_default_redis",
                return_value=MagicMock(get=lambda key: None),
            ),
        ):
            django_router.routers = [EnterpriseClusterRouter()]
            payments_api._get_line = record_get_line
            client = Client(raise_request_exception=False)

            with tenant_rls_scope(tenant_id, using="default"):
                before = _snapshot(line_id, using="default")
                outbox_before = OutboxRecord.objects.using("default").count()
            assert connections["default"].in_atomic_block is False
            attach = client.post(
                f"/api/v1/payments/statements/{payload['statement_id']}/lines/{line_id}/match/",
                data=json.dumps({"transaction_id": str(transaction_id)}),
                content_type="application/json",
            )
            with tenant_rls_scope(tenant_id, using="default"):
                after_attach = _snapshot(line_id, using="default")
                outbox_after_attach = OutboxRecord.objects.using("default").count()
            assert attach.status_code == 200
            assert after_attach is not None
            assert after_attach["match_status"] == "MATCHED"
            assert after_attach["version"] == before["version"] + 1
            assert str(after_attach["transaction_id"]) == str(transaction_id)
            assert outbox_after_attach == outbox_before + 1

            assert connections["default"].in_atomic_block is False
            detach = client.delete(
                f"/api/v1/payments/statements/{payload['statement_id']}/lines/{line_id}/match/"
            )
            with tenant_rls_scope(tenant_id, using="default"):
                after_detach = _snapshot(line_id, using="default")
                outbox_after_detach = OutboxRecord.objects.using("default").count()
            assert detach.status_code == 200
            assert after_detach is not None
            assert after_detach["match_status"] == "UNMATCHED"
            assert after_detach["version"] == before["version"] + 2
            assert after_detach["transaction_id"] is None
            assert outbox_after_detach == outbox_after_attach + 1
            assert selected_aliases == ["default", "default"]
    finally:
        payments_api._get_line = original_get_line
        django_router.routers = original_routers
        ContextCore.tenant_id.reset(token_tenant)
        ContextCore.user_id.reset(token_actor)
        ContextCore.force_primary.reset(force_token)

    commands = [
        {
            "name": "attach",
            "status": attach.status_code,
            "state_before": before,
            "state_after": after_attach,
            "outbox_before": outbox_before,
            "outbox_after": outbox_after_attach,
            "primary_alias": selected_aliases[0],
        },
        {
            "name": "detach",
            "status": detach.status_code,
            "state_before": after_attach,
            "state_after": after_detach,
            "outbox_before": outbox_after_attach,
            "outbox_after": outbox_after_detach,
            "primary_alias": selected_aliases[1],
        },
    ]
    result = {"action": "commands", "commands": commands, "selected_aliases": selected_aliases}
    return _write(output, result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "lag-write", "observe", "commands", "recovered"))
    parser.add_argument("--ids", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _setup()
    if args.action == "prepare":
        prepare(args.output)
    elif args.ids is None:
        parser.error("--ids is required for this action")
    elif args.action == "lag-write":
        lag_write(args.ids, args.output)
    elif args.action == "observe":
        observe(args.ids, args.output)
    elif args.action == "commands":
        commands(args.ids, args.output)
    else:
        recovered(args.ids, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
