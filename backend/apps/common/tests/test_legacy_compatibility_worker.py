"""Actual Celery prefork worker, broker, PostgreSQL, MinIO and ClamAV."""

from __future__ import annotations

import os
import subprocess
import time
import uuid

import pytest
from celery.result import AsyncResult
from django.conf import settings

from apps.common.contracts.legacy_document_contracts import get_legacy_compatibility_service
from apps.common.models.legacy_task_recovery import LegacyTaskRecovery
from apps.common.services.legacy_recovery_parking import load_parked
from apps.common.tenancy.context import tenant_rls_scope
from apps.contracts.services.legacy_document_mapping_service import LegacyDocumentMappingService
from apps.files.repositories.legacy_file_proof_adapter import LegacyFileProofAdapter
from config.celery import app

pytest_plugins = ["apps.common.tests.legacy_compat_fixtures"]
pytestmark = pytest.mark.django_db(transaction=True)
TASK = "apps.common.services.file_pipeline.orchestrator.run_antivirus_scan_task"


def _await(predicate, seconds=45):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.2)
    raise AssertionError("real worker did not reach the required durable state")


@pytest.fixture
def worker(tmp_path):
    name = f"legacy-compat-{uuid.uuid4().hex[:10]}@owned"
    log = (tmp_path / "worker.log").open("w")
    proc = subprocess.Popen(
        [
            "celery",
            "-A",
            "config.celery",
            "worker",
            "-Q",
            "files",
            "--pool=prefork",
            "--concurrency=1",
            "--loglevel=WARNING",
            f"--hostname={name}",
        ],
        cwd="/app",
        env=os.environ.copy(),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    _await(lambda: app.control.ping(destination=[name], timeout=0.5), seconds=25)
    yield proc
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    log.close()


def _record_state(task_id, tenant):
    with tenant_rls_scope(tenant):
        record = LegacyTaskRecovery.objects.filter(task_id=task_id).first()
        return record.status if record else None


def test_real_worker_parks_unresolved_then_replays_verified_mapping_and_deduplicates(
    worker,
    legacy_authenticated_request,
    legacy_export,
):
    request, auth = legacy_authenticated_request
    source, file_id, verifier = legacy_export(auth.tenant_id)
    task_id = str(uuid.uuid4())
    app.send_task(
        TASK,
        args=[str(source.legacy_document_id)],
        task_id=task_id,
        queue="files",
        headers={"x-ctx-tenant-id": str(auth.tenant_id)},
    )
    _await(lambda: AsyncResult(task_id, app=app).state == "LEGACY_UNRESOLVED")
    assert load_parked(task_id)["compat_legacy_doc_id"] == str(source.legacy_document_id)
    with tenant_rls_scope(auth.tenant_id):
        assert not LegacyTaskRecovery.objects.filter(task_id=task_id).exists()
        mapping = LegacyDocumentMappingService(verifier, LegacyFileProofAdapter()).record_verified(
            source, file_id, request
        )
    assert mapping.target_file_id != source.legacy_document_id
    service = get_legacy_compatibility_service()
    assert service.authorize_replay(task_id, request) == task_id
    _await(lambda: _record_state(task_id, auth.tenant_id) == "COMPLETED")
    assert LegacyFileProofAdapter().get_proof(file_id, auth.tenant_id).status == "AVAILABLE"
    assert AsyncResult(task_id, app=app).get(timeout=10) == "COMPLETED"
    with tenant_rls_scope(auth.tenant_id):
        record = LegacyTaskRecovery.objects.get(task_id=task_id)
        from apps.common.models.outbox import OutboxRecord

        assert OutboxRecord.objects.filter(event_type="files.FileAvailable", correlation_id=record.id).count() == 1
    assert record.scan_proof["clean"] is True


def test_double_durability_failure_stops_worker_and_requeues_original_envelope(worker):
    import redis

    from apps.common.infra.redis import get_streams_redis

    streams = get_streams_redis()
    broker = redis.Redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)
    task_id, doc_id = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        streams.config_set("appendonly", "no")
        app.send_task(TASK, args=[doc_id], task_id=task_id, queue="files")
        _await(lambda: worker.poll() is not None, seconds=30)
        assert worker.returncode == 78
        assert broker.llen("files") >= 1
        assert AsyncResult(task_id, app=app).state != "SUCCESS"
    finally:
        streams.config_set("appendonly", "yes")
        _await(lambda: streams.info("persistence")["aof_rewrite_in_progress"] == 0)
        assert int(streams.execute_command("WAITAOF", 1, 0, 5000)[0]) == 1
        # Remove only the synthetic queued envelope, preserving any other message.
        import json

        for raw in broker.lrange("files", 0, -1):
            if json.loads(raw).get("headers", {}).get("id") == task_id:
                broker.lrem("files", 1, raw)
        broker.close()


def test_controlled_reconciliation_requires_real_worker_quiescence(worker, legacy_authenticated_request):
    import hashlib

    from apps.common.repositories.legacy_task_recovery_repository import LegacyTaskRecoveryRepository as Recovery

    request, auth = legacy_authenticated_request
    task_id, doc_id = str(uuid.uuid4()), str(uuid.uuid4())
    with tenant_rls_scope(auth.tenant_id):
        record = Recovery.capture(
            task_id=task_id,
            doc_id=doc_id,
            task_name=TASK,
            args=(doc_id,),
            kwargs={},
            metadata={},
            tenant_id=auth.tenant_id,
        )
        assert Recovery.authorize_replay(
            record_id=record.id,
            tenant_id=auth.tenant_id,
            actor_id=auth.actor_id,
            expected_version=record.version,
            capability_sha256=hashlib.sha256(b"synthetic-crash-snapshot").hexdigest(),
        )
        record.refresh_from_db()
        assert Recovery.claim_processing(record_id=record.id, expected_version=record.version)
    get_legacy_compatibility_service().reconcile_processing(task_id, request)
    assert _record_state(task_id, auth.tenant_id) == "FAILED"
