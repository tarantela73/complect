"""
Tests for ContractEventLog read projection (Outbox → timeline).
Verifies: tenant isolation, event ordering, label mapping, empty result.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from apps.common.models.outbox import OutboxRecord
from apps.common.tenancy.context import set_tenant_context
from apps.contracts.queries.event_log_queries import get_contract_event_log


def _insert_event(event_type: str, contract_id: uuid.UUID, tenant_id: uuid.UUID, user_id: uuid.UUID) -> OutboxRecord:
    payload = {
        "event_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
        "contract_id": str(contract_id),
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "event_version": 1,
    }
    return OutboxRecord.objects.create(
        event_type=event_type,
        payload_json=payload,
        correlation_id=uuid.UUID(payload["correlation_id"]),
    )


@pytest.mark.django_db
class TestGetContractEventLog:
    def setup_method(self):
        self.tenant_id = uuid.uuid4()
        self.contract_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    def test_returns_empty_for_no_events(self):
        result = get_contract_event_log(self.contract_id, self.tenant_id)
        assert result.count == 0
        assert result.results == []

    def test_returns_single_created_event(self):
        _insert_event("contracts.ContractCreated", self.contract_id, self.tenant_id, self.user_id)
        result = get_contract_event_log(self.contract_id, self.tenant_id)
        assert result.count == 1
        assert result.results[0].event_type == "contracts.ContractCreated"
        assert result.results[0].label == "Создан"
        assert result.results[0].user_id == self.user_id

    def test_returns_all_lifecycle_events_in_order(self):
        _insert_event("contracts.ContractCreated", self.contract_id, self.tenant_id, self.user_id)
        _insert_event("contracts.ContractActivated", self.contract_id, self.tenant_id, self.user_id)
        _insert_event("contracts.ContractClosed", self.contract_id, self.tenant_id, self.user_id)
        result = get_contract_event_log(self.contract_id, self.tenant_id)
        assert result.count == 3
        types = [e.event_type for e in result.results]
        assert types == [
            "contracts.ContractCreated",
            "contracts.ContractActivated",
            "contracts.ContractClosed",
        ]

    def test_label_mapping(self):
        _insert_event("contracts.ContractActivated", self.contract_id, self.tenant_id, self.user_id)
        _insert_event("contracts.ContractClosed", self.contract_id, self.tenant_id, self.user_id)
        result = get_contract_event_log(self.contract_id, self.tenant_id)
        labels = [e.label for e in result.results]
        assert "Активирован" in labels
        assert "Закрыт" in labels

    def test_cross_tenant_isolation(self):
        """События другого тенанта не видны."""
        other_tenant = uuid.uuid4()
        _insert_event("contracts.ContractCreated", self.contract_id, other_tenant, self.user_id)
        result = get_contract_event_log(self.contract_id, self.tenant_id)
        assert result.count == 0

    def test_cross_contract_isolation(self):
        """События другого договора не видны."""
        other_contract = uuid.uuid4()
        _insert_event("contracts.ContractCreated", other_contract, self.tenant_id, self.user_id)
        result = get_contract_event_log(self.contract_id, self.tenant_id)
        assert result.count == 0

    def test_ignores_non_contract_event_types(self):
        """Unrelated outbox events (files, finance) are excluded."""
        _insert_event("files.FileUploaded", self.contract_id, self.tenant_id, self.user_id)
        result = get_contract_event_log(self.contract_id, self.tenant_id)
        assert result.count == 0
