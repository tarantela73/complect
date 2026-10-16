from __future__ import annotations

import uuid

import pytest
from django.core.exceptions import PermissionDenied

from apps.common.models.legacy_task_recovery import LegacyTaskRecovery
from apps.common.repositories.legacy_task_recovery_repository import LegacyTaskRecoveryRepository as Recovery
from apps.common.services.file_pipeline.orchestrator import run_antivirus_scan_task
from apps.common.services.legacy_recovery_parking import load_parked, park
from apps.common.tenancy.context import tenant_rls_scope


@pytest.mark.django_db(transaction=True)
def test_recovery_sql_is_tenant_scoped_and_repeat_preserves_original_envelope():
    tenant, task_id, doc_id = uuid.uuid4(), str(uuid.uuid4()), str(uuid.uuid4())
    with tenant_rls_scope(tenant):
        first = Recovery.capture(
            task_id=task_id,
            doc_id=doc_id,
            task_name=run_antivirus_scan_task.name,
            args=(doc_id,),
            kwargs={},
            metadata={},
            tenant_id=tenant,
        )
        repeated = Recovery.capture(
            task_id=task_id,
            doc_id=doc_id,
            task_name=run_antivirus_scan_task.name,
            args=(doc_id,),
            kwargs={},
            metadata={"delivery": "duplicate"},
            tenant_id=tenant,
        )
        assert repeated.id == first.id and repeated.metadata_payload == {}
        with pytest.raises(PermissionDenied, match="PAYLOAD_CONFLICT"):
            Recovery.capture(
                task_id=task_id,
                doc_id=doc_id,
                task_name=run_antivirus_scan_task.name,
                args=("different",),
                kwargs={},
                metadata={},
                tenant_id=tenant,
            )
    with tenant_rls_scope(uuid.uuid4()):
        assert not LegacyTaskRecovery.objects.filter(task_id=task_id).exists()
    assert not LegacyTaskRecovery.objects.filter(task_id=task_id).exists()


@pytest.mark.django_db(transaction=True)
def test_unresolved_and_completed_records_cannot_receive_scan_checkpoint():
    tenant, doc_id = uuid.uuid4(), str(uuid.uuid4())
    with tenant_rls_scope(tenant):
        record = Recovery.capture(
            task_id=str(uuid.uuid4()),
            doc_id=doc_id,
            task_name=run_antivirus_scan_task.name,
            args=(doc_id,),
            kwargs={},
            metadata={},
            tenant_id=tenant,
        )
        assert not Recovery.store_scan_proof(
            record_id=record.id, expected_version=record.version, scan_proof={"clean": True}
        )
        assert Recovery.authorize_replay(
            record_id=record.id,
            tenant_id=tenant,
            actor_id=uuid.uuid4(),
            expected_version=record.version,
            capability_sha256="a" * 64,
        )
        record.refresh_from_db()
        assert Recovery.claim_processing(record_id=record.id, expected_version=record.version)
        assert not Recovery.claim_processing(record_id=record.id, expected_version=record.version)
        record.refresh_from_db()
        assert Recovery.record_completed(record_id=record.id, expected_version=record.version)
        record.refresh_from_db()
        assert not Recovery.record_failure(record_id=record.id, expected_version=record.version, reason="late failure")


def test_durable_transport_parking_is_idempotent_and_preserves_unknown_form():
    task_id, doc_id = str(uuid.uuid4()), str(uuid.uuid4())
    args, kwargs = (doc_id, "unknown-v2"), {"future": "keep-for-reconciliation"}
    first = park(task_id, doc_id, args, kwargs, {}, "LEGACY_ENVELOPE_SHAPE_UNSUPPORTED")
    assert park(task_id, doc_id, args, kwargs, {}, "duplicate") == first
    envelope = load_parked(task_id)
    import json

    assert json.loads(envelope["args"]) == list(args)
    assert json.loads(envelope["kwargs"]) == kwargs
    assert envelope["compat_legacy_doc_id"] == doc_id
    assert run_antivirus_scan_task.name == "apps.common.services.file_pipeline.orchestrator.run_antivirus_scan_task"
    assert run_antivirus_scan_task.acks_late and run_antivirus_scan_task.reject_on_worker_lost
