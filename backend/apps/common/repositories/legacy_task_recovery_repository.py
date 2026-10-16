"""Tenant-scoped SQL evidence after controlled resolution of transport quarantine."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.common.models.legacy_task_recovery import LegacyTaskRecovery, LegacyTaskRecoveryStatus
from apps.common.tenancy.context import get_current_tenant_id


class LegacyTaskRecoveryRepository:
    @staticmethod
    def capture(
        *,
        task_id: str,
        doc_id: str,
        task_name: str,
        args: tuple[object, ...],
        kwargs: Mapping[str, object],
        metadata: Mapping[str, object],
        tenant_id: uuid.UUID,
        retries: int = 0,
    ) -> LegacyTaskRecovery:
        if tenant_id != get_current_tenant_id():
            raise PermissionDenied("LEGACY_RECOVERY_TENANT_MISMATCH")
        values = dict(
            doc_id=doc_id,
            task_name=task_name,
            args_payload=list(args),
            kwargs_payload=dict(kwargs),
            metadata_payload=dict(metadata),
            tenant_id=tenant_id,
            retries=retries,
        )
        try:
            with transaction.atomic():
                record, created = LegacyTaskRecovery.objects.get_or_create(
                    task_id=task_id,
                    tenant_id=tenant_id,
                    defaults=values,
                )
        except IntegrityError as exc:
            raise PermissionDenied("LEGACY_TASK_ID_CONFLICT") from exc
        if not created and (
            record.doc_id != doc_id
            or record.task_name != task_name
            or record.args_payload != list(args)
            or record.kwargs_payload != dict(kwargs)
        ):
            raise PermissionDenied("LEGACY_REPLAY_PAYLOAD_CONFLICT")
        return record

    @staticmethod
    def get(record_id: uuid.UUID) -> LegacyTaskRecovery:
        return LegacyTaskRecovery.objects.get(id=record_id, tenant_id=get_current_tenant_id())

    @staticmethod
    def get_by_task(task_id: str) -> LegacyTaskRecovery:
        return LegacyTaskRecovery.objects.get(task_id=task_id, tenant_id=get_current_tenant_id())

    @staticmethod
    def authorize_replay(
        *,
        record_id: uuid.UUID,
        tenant_id: uuid.UUID,
        actor_id: uuid.UUID,
        expected_version: int,
        capability_sha256: str,
    ) -> bool:
        if tenant_id != get_current_tenant_id() or len(capability_sha256) != 64:
            raise PermissionDenied("LEGACY_REPLAY_ASSIGNMENT_INVALID")
        return (
            LegacyTaskRecovery.objects.filter(
                id=record_id,
                tenant_id=tenant_id,
                version=expected_version,
                status__in=[
                    LegacyTaskRecoveryStatus.UNRESOLVED,
                    LegacyTaskRecoveryStatus.FAILED,
                    LegacyTaskRecoveryStatus.READY_TO_REPLAY,
                ],
            ).update(
                assigned_by_actor_id=actor_id,
                assigned_at=timezone.now(),
                capability_sha256=capability_sha256,
                status=LegacyTaskRecoveryStatus.READY_TO_REPLAY,
                version=F("version") + 1,
                updated_at=timezone.now(),
            )
            == 1
        )

    @staticmethod
    def store_scan_proof(*, record_id: uuid.UUID, expected_version: int, scan_proof: Mapping[str, object]) -> bool:
        return (
            LegacyTaskRecovery.objects.filter(
                id=record_id,
                tenant_id=get_current_tenant_id(),
                version=expected_version,
                status=LegacyTaskRecoveryStatus.PROCESSING,
            ).update(scan_proof=dict(scan_proof), version=F("version") + 1, updated_at=timezone.now())
            == 1
        )

    @staticmethod
    def claim_processing(*, record_id: uuid.UUID, expected_version: int) -> bool:
        return (
            LegacyTaskRecovery.objects.filter(
                id=record_id,
                tenant_id=get_current_tenant_id(),
                version=expected_version,
                status=LegacyTaskRecoveryStatus.READY_TO_REPLAY,
            ).update(status=LegacyTaskRecoveryStatus.PROCESSING, version=F("version") + 1, updated_at=timezone.now())
            == 1
        )

    @staticmethod
    def record_completed(*, record_id: uuid.UUID, expected_version: int) -> bool:
        return (
            LegacyTaskRecovery.objects.filter(
                id=record_id,
                tenant_id=get_current_tenant_id(),
                version=expected_version,
                status=LegacyTaskRecoveryStatus.PROCESSING,
            ).update(
                status=LegacyTaskRecoveryStatus.COMPLETED,
                completed_at=timezone.now(),
                version=F("version") + 1,
                updated_at=timezone.now(),
            )
            == 1
        )

    @staticmethod
    def record_failure(*, record_id: uuid.UUID, expected_version: int, reason: str) -> bool:
        return (
            LegacyTaskRecovery.objects.filter(
                id=record_id,
                tenant_id=get_current_tenant_id(),
                version=expected_version,
                status__in=[LegacyTaskRecoveryStatus.PROCESSING, LegacyTaskRecoveryStatus.READY_TO_REPLAY],
            ).update(
                status=LegacyTaskRecoveryStatus.FAILED,
                failure_reason=reason[:255],
                version=F("version") + 1,
                updated_at=timezone.now(),
            )
            == 1
        )
