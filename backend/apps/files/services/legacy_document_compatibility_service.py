"""Controlled compatibility replay. Transport headers alone confer no authority."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from typing import NoReturn

from celery.exceptions import Ignore
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, transaction
from django.http import HttpRequest

from apps.common.api.auth import verified_request_context
from apps.common.contracts.legacy_document_contracts import FileProof, LegacyLinkProtocol, RecoveryPersistenceError
from apps.common.models.legacy_task_recovery import LegacyTaskRecoveryStatus
from apps.common.repositories.legacy_task_recovery_repository import LegacyTaskRecoveryRepository
from apps.common.security.policy_engine import PolicyEngine
from apps.common.services.legacy_recovery_parking import load_parked, park
from apps.common.tenancy.context import tenant_rls_scope
from apps.files.repositories.legacy_file_proof_adapter import LegacyFileProofAdapter
from apps.files.services.legacy_document_scan_adapter import LegacyDocumentScanAdapter

TASK_NAME = "apps.common.services.file_pipeline.orchestrator.run_antivirus_scan_task"


def envelope_sha256(args: tuple, kwargs: dict) -> str:
    encoded = json.dumps(
        {"args": list(args), "kwargs": kwargs}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class LegacyDocumentCompatibilityService:
    def __init__(self, links: LegacyLinkProtocol) -> None:
        self.links = links
        self.files = LegacyFileProofAdapter()

    def submit(self, doc_id: uuid.UUID) -> str:
        # The old UUID cannot identify a File. Persist unresolved before publishing.
        task_id = str(uuid.uuid4())
        park(task_id, str(doc_id), (str(doc_id),), {}, {}, "LEGACY_UNRESOLVED")
        from config.celery import app

        transaction.on_commit(lambda: app.send_task(TASK_NAME, args=[str(doc_id)], task_id=task_id, queue="files"))
        return task_id

    def authorize_replay(self, task_id: str, request: HttpRequest) -> str:
        auth = verified_request_context(request)
        if auth is None or settings.RBAC_ENFORCEMENT_MODE == "OFF":
            raise PermissionDenied("LEGACY_VERIFIED_OPERATOR_REQUIRED")
        with tenant_rls_scope(auth.tenant_id):
            if not PolicyEngine.check(
                actor_id=auth.actor_id, tenant_id=auth.tenant_id, action="write", resource="legacy_document_mapping"
            ):
                raise PermissionDenied("LEGACY_REPLAY_PERMISSION_REQUIRED")
            envelope = load_parked(task_id)
            doc_id = envelope["compat_legacy_doc_id"]
            args, kwargs = tuple(json.loads(envelope["args"])), json.loads(envelope["kwargs"])
            if not ((args == (doc_id,) and kwargs == {}) or (args == () and kwargs == {"doc_id": doc_id})):
                raise PermissionDenied("LEGACY_ENVELOPE_SHAPE_UNSUPPORTED")
            link = self.links.resolve(uuid.UUID(doc_id), auth.tenant_id)
            if link is None or link.source.payload_sha256 != envelope_sha256(args, kwargs):
                raise PermissionDenied("LEGACY_VERIFIED_MAPPING_REQUIRED")
            record = LegacyTaskRecoveryRepository.capture(
                task_id=task_id,
                doc_id=doc_id,
                task_name=TASK_NAME,
                args=args,
                kwargs=kwargs,
                metadata={},
                tenant_id=auth.tenant_id,
            )
            if record.status in {LegacyTaskRecoveryStatus.PROCESSING, LegacyTaskRecoveryStatus.COMPLETED}:
                raise PermissionDenied("LEGACY_REPLAY_REQUIRES_RECONCILIATION")
            capability = secrets.token_urlsafe(32)
            digest = hashlib.sha256(capability.encode()).hexdigest()
            # Scope + expected version + complete authority assignment in one CAS.
            updated = LegacyTaskRecoveryRepository.authorize_replay(
                record_id=record.id,
                expected_version=record.version,
                tenant_id=auth.tenant_id,
                actor_id=auth.actor_id,
                capability_sha256=digest,
            )
            if not updated:
                raise PermissionDenied("LEGACY_REPLAY_CAS_CONFLICT")
            from config.celery import app

            args, kwargs = tuple(record.args_payload), dict(record.kwargs_payload)
            transaction.on_commit(
                lambda: app.send_task(
                    TASK_NAME,
                    args=args,
                    kwargs=kwargs,
                    task_id=task_id,
                    queue="files",
                    headers={"compat_tenant": str(auth.tenant_id), "compat_token": capability},
                )
            )
        return task_id

    def reconcile_processing(self, task_id: str, request: HttpRequest) -> None:
        """Explicit repair after every responding worker confirms execution quiescence."""
        auth = verified_request_context(request)
        if auth is None or settings.RBAC_ENFORCEMENT_MODE == "OFF":
            raise PermissionDenied("LEGACY_VERIFIED_OPERATOR_REQUIRED")
        with tenant_rls_scope(auth.tenant_id):
            if not PolicyEngine.check(
                actor_id=auth.actor_id, tenant_id=auth.tenant_id, action="write", resource="legacy_document_mapping"
            ):
                raise PermissionDenied("LEGACY_REPLAY_PERMISSION_REQUIRED")
            record = LegacyTaskRecoveryRepository.get_by_task(task_id)
            if record.status != LegacyTaskRecoveryStatus.PROCESSING:
                raise PermissionDenied("LEGACY_RECONCILIATION_STATE_CHANGED")
        from config.celery import app

        inspector = app.control.inspect(timeout=3)
        workers = inspector.ping()
        if not workers:
            raise PermissionDenied("LEGACY_WORKER_QUIESCENCE_UNVERIFIED")
        for replies in (inspector.active(), inspector.reserved(), inspector.scheduled()):
            if not replies or set(workers) - set(replies):
                raise PermissionDenied("LEGACY_WORKER_QUIESCENCE_UNVERIFIED")
            for tasks in replies.values():
                for task in tasks:
                    info = task.get("request", task)
                    if not isinstance(info, dict):
                        raise PermissionDenied("LEGACY_WORKER_QUIESCENCE_UNVERIFIED")
                    if str(info.get("id")) == task_id:
                        raise PermissionDenied("LEGACY_EXECUTION_STILL_ACTIVE")
        with tenant_rls_scope(auth.tenant_id):
            if not LegacyTaskRecoveryRepository.record_failure(
                record_id=record.id,
                expected_version=record.version,
                reason="CONTROLLED_QUIESCED_RECONCILIATION",
            ):
                raise PermissionDenied("LEGACY_RECONCILIATION_CAS_CONFLICT")

    def run(self, doc_id: str, task_id: str, args: tuple, kwargs: dict, metadata: dict) -> str:
        token = metadata.get("compat_token")
        tenant = metadata.get("compat_tenant")
        if not isinstance(token, str) or not isinstance(tenant, str):
            # No SQL tenant can be inferred from an unsigned historical envelope.
            # The task wrapper parks it in the durable, operator-only transport DLQ.
            raise RecoveryPersistenceError("LEGACY_UNRESOLVED")
        tenant_id = uuid.UUID(tenant)
        with tenant_rls_scope(tenant_id):
            record = LegacyTaskRecoveryRepository.get_by_task(task_id)
            if not hmac.compare_digest(record.capability_sha256, hashlib.sha256(token.encode()).hexdigest()):
                raise PermissionDenied("LEGACY_REPLAY_CAPABILITY_INVALID")
            if record.doc_id != doc_id or record.args_payload != list(args) or record.kwargs_payload != kwargs:
                raise PermissionDenied("LEGACY_REPLAY_PAYLOAD_CHANGED")

            def fail_authorized(reason: str) -> NoReturn:
                if not LegacyTaskRecoveryRepository.record_failure(
                    record_id=record.id,
                    expected_version=record.version,
                    reason=reason,
                ):
                    raise RecoveryPersistenceError(reason)
                raise Ignore(reason)

            link = self.links.resolve(uuid.UUID(doc_id), tenant_id)
            if link is None or link.source.payload_sha256 != envelope_sha256(args, kwargs):
                fail_authorized("LEGACY_MAPPING_UNRESOLVED")
            if record.status != LegacyTaskRecoveryStatus.COMPLETED:
                # PROCESSING cannot be reclaimed implicitly while another worker performs I/O.
                if (
                    record.status != LegacyTaskRecoveryStatus.READY_TO_REPLAY
                    or not LegacyTaskRecoveryRepository.claim_processing(
                        record_id=record.id,
                        expected_version=record.version,
                    )
                ):
                    raise Ignore("LEGACY_IN_PROGRESS_OR_FAILED")
                record.refresh_from_db()
            proof = self.files.get_proof(link.target_file_id, tenant_id)
            if (
                proof.tenant_id != link.source.tenant_id
                or proof.object_key != link.source.object_key
                or proof.checksum_sha256 != link.source.checksum_sha256
                or proof.size_bytes != link.source.size_bytes
            ):
                fail_authorized("LEGACY_TARGET_PROOF_CHANGED")
            bound_scan = {
                "checksum": link.source.checksum_sha256,
                "key": link.source.object_key,
                "namespace": link.source.namespace,
                "mapping_version": link.version,
                "file_id": str(link.target_file_id),
            }
            checkpoint = record.scan_proof
            if checkpoint and any(checkpoint.get(key) != value for key, value in bound_scan.items()):
                fail_authorized("LEGACY_SCAN_CHECKPOINT_MAPPING_CHANGED")
            clean = checkpoint.get("clean") is True
            expected_file_version = checkpoint.get("file_version", link.target_version)
            if proof.status == "AVAILABLE" and clean and isinstance(expected_file_version, int):
                expected_file_version += 1
            if proof.version != expected_file_version:
                fail_authorized("LEGACY_FILE_EXPECTED_VERSION_CHANGED")

        def record_checkpoint(scanning_proof: FileProof, clean_result: bool) -> None:
            with tenant_rls_scope(tenant_id):
                if not LegacyTaskRecoveryRepository.store_scan_proof(
                    record_id=record.id,
                    expected_version=record.version,
                    scan_proof={**bound_scan, "file_version": scanning_proof.version, "clean": clean_result},
                ):
                    raise RecoveryPersistenceError("LEGACY_SCAN_CHECKPOINT_CAS_CONFLICT")
                record.refresh_from_db()

        def revalidate() -> None:
            with tenant_rls_scope(tenant_id):
                if self.links.resolve(uuid.UUID(doc_id), tenant_id) != link:
                    raise PermissionDenied("LEGACY_MAPPING_CHANGED_DURING_SCAN")

        def finalize(scanning_proof: FileProof) -> None:
            def transition() -> None:
                if scanning_proof.status == "AVAILABLE":
                    self.files.confirm_available(scanning_proof)
                else:
                    self.files.transition(scanning_proof, status="AVAILABLE", correlation_id=record.id)

            self.links.finalize_resolved(link, transition)

        try:
            result = LegacyDocumentScanAdapter().process(
                link,
                proof,
                correlation_id=record.id,
                clean_checkpoint=clean,
                record_checkpoint=record_checkpoint,
                revalidate_link=revalidate,
                finalize=finalize,
            )
            with tenant_rls_scope(tenant_id):
                if result == "COMPLETED":
                    if (
                        record.status != LegacyTaskRecoveryStatus.COMPLETED
                        and not LegacyTaskRecoveryRepository.record_completed(
                            record_id=record.id,
                            expected_version=record.version,
                        )
                    ):
                        raise RecoveryPersistenceError("LEGACY_COMPLETE_CHECKPOINT_FAILED")
                    return "COMPLETED"
                if not LegacyTaskRecoveryRepository.record_failure(
                    record_id=record.id,
                    expected_version=record.version,
                    reason=result,
                ):
                    raise RecoveryPersistenceError("LEGACY_FAILURE_CHECKPOINT_FAILED")
        except RecoveryPersistenceError:
            raise
        except Exception as exc:
            try:
                with tenant_rls_scope(tenant_id):
                    if not LegacyTaskRecoveryRepository.record_failure(
                        record_id=record.id,
                        expected_version=record.version,
                        reason=exc.__class__.__name__,
                    ):
                        raise RecoveryPersistenceError("LEGACY_FAILURE_CHECKPOINT_FAILED")
            except DatabaseError as db_exc:
                raise RecoveryPersistenceError("LEGACY_FAILURE_CHECKPOINT_FAILED") from db_exc
            raise Ignore("LEGACY_RECOVERY_REQUIRED") from exc
        raise Ignore("LEGACY_INFECTED")
