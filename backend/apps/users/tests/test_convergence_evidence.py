import json

import pyotp
import pytest
from django.db import transaction

from apps.common.models.outbox import OutboxRecord
from apps.users.events import IdentityMergeAuthorityChangedEvent
from apps.users.models import IdentityMergeOperation, IdentitySecurityAuditRecord, User
from apps.users.models_identity import MergeActionKind
from apps.users.services.merge_convergence_service import MergeConvergenceService


def _evidence(operation):
    audits = IdentitySecurityAuditRecord.objects.using("default").filter(
        event_type="identity.merge_authority_changed",
        metadata__operation_id=str(operation.id),
    )
    outbox = OutboxRecord.objects.using("default").filter(
        event_type=IdentityMergeAuthorityChangedEvent.EVENT_TYPE,
        payload_json__operation_id=str(operation.id),
    )
    return audits, outbox


def _approve_owner(collision_pair, signed_reconciliation_approval, resolution_service):
    source, canonical = collision_pair.second, collision_pair.first
    secret = pyotp.random_base32()
    User.objects.filter(pk=canonical.pk).update(mfa_enabled=True, mfa_secret=secret)
    canonical.refresh_from_db(using="default")
    approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-EVIDENCE-OWNER",
    )
    operation = resolution_service.approve_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        approval=approval,
    )
    return source, canonical, secret, operation


@pytest.mark.django_db(transaction=True)
def test_owner_convergence_commits_exactly_one_pseudonymous_audit_and_outbox(
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
    valid_runtime_merge_coverage_attestation,
):
    source, _canonical, secret, operation = _approve_owner(
        collision_pair,
        signed_reconciliation_approval,
        identity_collision_resolution_service,
    )
    service = MergeConvergenceService()
    challenge = service.start(operation_id=operation.id, actor_pk=source.pk)
    kwargs = {
        "challenge_token": challenge.challenge_token,
        "actor_pk": source.pk,
        "new_password": "Evidence Owner Password 50!",
        "totp_code": pyotp.TOTP(secret).now(),
        "idempotency_key": challenge.idempotency_key,
    }
    service.complete(**kwargs)
    service.complete(**kwargs)

    audits, outbox = _evidence(operation)
    assert audits.count() == 1
    assert outbox.count() == 1
    durable = json.dumps({"audit": audits.first().metadata, "outbox": outbox.first().payload_json})
    assert "user_pk" not in durable
    assert "legacy_user_pk" not in durable
    assert source.email not in durable
    assert source.username not in durable


@pytest.mark.django_db(transaction=True)
def test_dormant_convergence_commits_exactly_one_audit_and_outbox(
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
    valid_runtime_merge_coverage_attestation,
    settings,
):
    source, canonical = collision_pair.second, collision_pair.first
    User.objects.filter(pk__in=[source.pk, canonical.pk]).update(
        is_active=False,
        security_status=User.SecurityStatus.LOCKED,
    )
    source.refresh_from_db(using="default")
    canonical.refresh_from_db(using="default")
    approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-EVIDENCE-DORMANT",
    )
    operation = identity_collision_resolution_service.approve_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        approval=approval,
    )
    for participant in (source, canonical):
        keys = list(settings.REDIS_CLIENT.scan_iter(match=f"auth:refresh:{participant.pk}:*"))
        if keys:
            settings.REDIS_CLIENT.delete(*keys)

    result = MergeConvergenceService().complete_dormant(operation_id=operation.id)
    audits, outbox = _evidence(result)
    assert audits.count() == 1
    assert outbox.count() == 1
    assert outbox.get().payload_json["action_kind"] == MergeActionKind.DORMANT_CONVERGENCE


@pytest.mark.django_db(transaction=True)
def test_outer_transaction_failure_rolls_back_transition_and_evidence_together(
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
    valid_runtime_merge_coverage_attestation,
):
    source, _canonical, secret, operation = _approve_owner(
        collision_pair,
        signed_reconciliation_approval,
        identity_collision_resolution_service,
    )
    service = MergeConvergenceService()
    challenge = service.start(operation_id=operation.id, actor_pk=source.pk)

    with pytest.raises(RuntimeError, match="rollback outer transaction"):
        with transaction.atomic(using="default"):
            service.complete(
                challenge_token=challenge.challenge_token,
                actor_pk=source.pk,
                new_password="Rollback Evidence Password 51!",
                totp_code=pyotp.TOTP(secret).now(),
                idempotency_key=challenge.idempotency_key,
            )
            raise RuntimeError("rollback outer transaction")

    operation.refresh_from_db(using="default")
    audits, outbox = _evidence(operation)
    assert operation.state == IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE
    assert audits.count() == 0
    assert outbox.count() == 0
