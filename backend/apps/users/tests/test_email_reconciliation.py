import json
import stat
import uuid
from dataclasses import dataclass

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command

from apps.users.models import IdentityMergeOperation, IdentitySecurityAuditRecord, User
from apps.users.repositories.identity_collision_repository import IdentityCollisionRepository
from apps.users.services.email_normalization import normalize_email
from apps.users.services.identity_collision_resolution import (
    IdentityCollisionResolutionError,
    IdentityCollisionResolutionErrorCode,
)
from apps.users.services.pii_access_audit_service import PiiAccessAuditService, PiiAuditUnavailable


def _audit_args():
    return [
        "--actor-type", "operator",
        "--actor-ref", "iam-migration-operator",
        "--request-id", str(uuid.uuid4()),
        "--correlation-id", str(uuid.uuid4()),
    ]


@pytest.fixture
def protected_report_dir(tmp_path):
    path = tmp_path / "protected-reconciliation"
    path.mkdir(mode=0o700)
    return path


def test_normalization_v1_is_explicit_and_stable():
    assert normalize_email(" Ivan.Example@EXAMPLE.COM ").value == "ivan.example@example.com"
    assert normalize_email(" Ivan.Example@EXAMPLE.COM ").version == 1
    assert normalize_email("CAFÉ@EXAMPLE.COM").value == normalize_email("CAFE\u0301@example.com").value
    assert normalize_email("ｉｖａｎ＠ＥＸＡＭＰＬＥ．ＣＯＭ").value == "ivan@example.com"


@pytest.mark.parametrize("raw", ["   ", "missing-at.example.com", f"{'a' * 245}@example.com"])
def test_normalization_rejects_invalid_or_overlong_email(raw):
    with pytest.raises(ValidationError):
        normalize_email(raw)


@pytest.mark.django_db
def test_report_audits_before_writing_protected_collision_data(protected_report_dir):
    for email in ("Ivan@Example.com", "ivan@example.com"):
        identity_id = uuid.uuid4()
        User.objects.create(
            username=identity_id.hex,
            email=email,
            identity_id=identity_id,
            normalized_email=f"{identity_id}@example.invalid",
            email_normalization_version=1,
            security_version=1,
            security_status=User.SecurityStatus.ACTIVE,
        )
    output = protected_report_dir / "identity-email-collisions.json"
    call_command("report_identity_email_duplicates", "--output", str(output), *_audit_args())
    payload = json.loads(output.read_text())
    assert payload["unresolved_duplicate_groups"] == 1
    assert payload["groups"][0]["normalized_email"] == "ivan@example.com"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    audits = IdentitySecurityAuditRecord.objects.filter(reason_code="IDENTITY_EMAIL_RECONCILIATION_REPORT")
    assert audits.count() == 2
    assert all("@" not in json.dumps(row.metadata) for row in audits)


@pytest.mark.django_db
def test_report_refuses_repository_output():
    with pytest.raises(CommandError, match="outside the repository"):
        call_command(
            "report_identity_email_duplicates",
            "--output",
            str(settings.BASE_DIR / "identity-email-collisions.json"),
            *_audit_args(),
        )


@pytest.mark.django_db(transaction=True)
def test_report_commits_audit_before_pii_query_and_fails_closed(protected_report_dir, monkeypatch):
    user = User.objects.create(username="audited@example.com", email="audited@example.com")
    pii_query_called = False

    def load_pii_candidates(*args, **kwargs):
        nonlocal pii_query_called
        pii_query_called = True
        return []

    def fail_audit(*args, **kwargs):
        raise PiiAuditUnavailable("closed failure")

    monkeypatch.setattr(IdentityCollisionRepository, "load_pii_candidates", load_pii_candidates)
    monkeypatch.setattr(PiiAccessAuditService, "append_batch_and_commit", fail_audit)
    output = protected_report_dir / "identity-email-collisions.json"

    with pytest.raises(CommandError, match="PII audit unavailable"):
        call_command("report_identity_email_duplicates", "--output", str(output), *_audit_args())

    assert user.pk
    assert pii_query_called is False
    assert not output.exists()


@pytest.mark.django_db
def test_report_collects_invalid_identifiers_without_aborting(protected_report_dir):
    first = User.objects.create(username="not-an-email", email="")
    second = User.objects.create(username="", email="missing-at.example.com")
    output = protected_report_dir / "identity-email-collisions.json"

    call_command("report_identity_email_duplicates", "--output", str(output), *_audit_args())

    payload = json.loads(output.read_text())
    assert payload["invalid_candidates"]["count"] == 2
    assert {row["user_pk"] for row in payload["invalid_candidates"]["rows"]} == {first.pk, second.pk}
    with pytest.raises(CommandError, match="invalid identity candidates"):
        call_command(
            "report_identity_email_duplicates",
            "--output",
            str(protected_report_dir / "identity-email-invalid.json"),
            "--fail-on-invalid-candidates",
            *_audit_args(),
        )


@pytest.mark.django_db
def test_report_rejects_symlink_target_without_touching_referent(protected_report_dir):
    referent = protected_report_dir / "operator-owned.json"
    referent.write_text("do-not-overwrite")
    output = protected_report_dir / "identity-email-collisions.json"
    output.symlink_to(referent)

    with pytest.raises(CommandError, match="symlink or non-regular"):
        call_command("report_identity_email_duplicates", "--output", str(output), *_audit_args())

    assert referent.read_text() == "do-not-overwrite"


@pytest.mark.django_db(transaction=True)
def test_collision_resolution_requires_dry_run_and_approval_reference(collision_pair):
    with pytest.raises(CommandError, match="approval-reference"):
        call_command(
            "resolve_identity_collision",
            "--correct-email",
            str(collision_pair.second.pk),
            "--email",
            "corrected@example.com",
            "--execute",
        )

    call_command(
        "resolve_identity_collision",
        "--correct-email",
        str(collision_pair.second.pk),
        "--email",
        "corrected@example.com",
        "--approval-reference",
        "IAM-CHANGE-DRY-RUN",
        "--dry-run",
    )
    collision_pair.second.refresh_from_db()
    assert collision_pair.second.email != "corrected@example.com"


@pytest.mark.django_db(transaction=True)
def test_signed_merge_is_pending_and_does_not_change_authority(
    collision_pair,
    signed_reconciliation_approval,
):
    source, canonical = collision_pair.second, collision_pair.first
    before = (source.email, source.normalized_email, source.password, source.is_active)
    approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-CHANGE-43",
    )
    call_command(
        "resolve_identity_collision",
        "--merge", str(source.pk),
        "--into", str(canonical.pk),
        "--approval-reference", approval.reference,
        "--approval-key-id", approval.key_id,
        "--approval-signature", approval.signature,
        "--execute",
    )
    source.refresh_from_db()
    operation = IdentityMergeOperation.objects.get(source_user=source)
    assert operation.state == IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE
    assert (source.email, source.normalized_email, source.password, source.is_active) == before


@pytest.mark.django_db(transaction=True)
def test_pending_merge_must_be_cancelled_before_fresh_correction(
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
):
    source, canonical = collision_pair.second, collision_pair.first
    merge = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-MERGE",
    )
    operation = identity_collision_resolution_service.approve_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        approval=merge,
    )
    stale = signed_reconciliation_approval.for_correct_email(
        user_pk=source.pk,
        email="corrected@example.com",
        reference="IAM-CORRECT-STALE",
    )
    with pytest.raises(Exception, match="MERGE_OPERATION_ACTIVE"):
        identity_collision_resolution_service.correct_email(
            user_pk=source.pk,
            new_email="corrected@example.com",
            approval=stale,
        )
    cancel = signed_reconciliation_approval.for_cancel_merge(
        operation_id=operation.id,
        expected_version=operation.version,
        reference="IAM-CANCEL",
    )
    identity_collision_resolution_service.cancel_merge(operation_id=operation.id, approval=cancel)
    with pytest.raises(Exception, match="APPROVAL_CONTEXT_STALE"):
        identity_collision_resolution_service.correct_email(
            user_pk=source.pk,
            new_email="corrected@example.com",
            approval=stale,
        )
    fresh = signed_reconciliation_approval.for_correct_email(
        user_pk=source.pk,
        email="corrected@example.com",
        reference="IAM-CORRECT-FRESH",
    )
    identity_collision_resolution_service.correct_email(
        user_pk=source.pk,
        new_email="corrected@example.com",
        approval=fresh,
    )
    source.refresh_from_db()
    assert source.email == "corrected@example.com"


@pytest.mark.django_db(transaction=True)
def test_correct_email_initializes_nullable_expand_fields_before_cas(
    signed_reconciliation_approval,
    identity_collision_resolution_service,
):
    user = User.objects.create_user(
        username="legacy-null-foundation",
        email="legacy-null-foundation@example.com",
        password="Legacy Password 42!",
        tenant_id=uuid.uuid4(),
    )
    assert user.identity_id is None
    approval = signed_reconciliation_approval.for_correct_email(
        user_pk=user.pk,
        email="corrected-foundation@example.com",
        reference="IAM-CORRECT-NULL-FOUNDATION",
    )

    identity_collision_resolution_service.correct_email(
        user_pk=user.pk,
        new_email="corrected-foundation@example.com",
        approval=approval,
    )

    user.refresh_from_db(using="default")
    assert user.identity_id is not None
    assert user.normalized_email == "corrected-foundation@example.com"
    assert user.email_normalization_version == 1
    assert user.security_version == 2
    assert user.identity_legal_hold is False


@pytest.mark.django_db(transaction=True)
def test_opposite_pending_merge_is_rejected_before_it_can_create_a_cycle(
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
):
    source, canonical = collision_pair.second, collision_pair.first
    first_approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-CYCLE-FIRST",
    )
    identity_collision_resolution_service.approve_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        approval=first_approval,
    )
    opposite_approval = signed_reconciliation_approval.for_merge(
        source_pk=canonical.pk,
        canonical_pk=source.pk,
        reference="IAM-CYCLE-OPPOSITE",
    )

    with pytest.raises(IdentityCollisionResolutionError) as error:
        identity_collision_resolution_service.approve_merge(
            source_pk=canonical.pk,
            canonical_pk=source.pk,
            approval=opposite_approval,
        )
    assert error.value.code is IdentityCollisionResolutionErrorCode.MERGE_OPERATION_ACTIVE

    assert IdentityMergeOperation.objects.using("default").count() == 1


@dataclass(frozen=True)
class _PiiSubject:
    user_pk: int
    identity_id: uuid.UUID


@pytest.mark.django_db(transaction=True)
def test_pii_audit_subject_is_stable_distinct_and_never_persists_raw_pk():
    first_id, second_id = uuid.uuid4(), uuid.uuid4()
    first = _PiiSubject(101_001, first_id)
    second = _PiiSubject(202_002, second_id)
    request_id, correlation_id = uuid.uuid4(), uuid.uuid4()
    PiiAccessAuditService.append_batch_and_commit(
        subjects=[first, first, second],
        actor_type="operator",
        actor_ref="iam-test",
        reason_code="PII_PSEUDONYM_TEST",
        request_id=request_id,
        correlation_id=correlation_id,
    )
    rows = list(
        IdentitySecurityAuditRecord.objects.using("default")
        .filter(reason_code="PII_PSEUDONYM_TEST")
        .order_by("occurred_at", "id"),
    )
    assert rows[0].subject_hmac == rows[1].subject_hmac
    assert rows[0].subject_hmac != rows[2].subject_hmac
    assert len(rows[0].subject_hmac) == 64
    for row, raw_pk in zip(rows, [first.user_pk, first.user_pk, second.user_pk], strict=True):
        assert "user_pk" not in row.metadata
        assert raw_pk not in row.metadata.values()
        assert str(raw_pk) not in row.metadata.values()
