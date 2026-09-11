import uuid
from datetime import datetime

from django.conf import settings
from django.core.management import BaseCommand, CommandError
from django.utils import timezone

from apps.users.models import IdentityMergeHoldBlock, IdentityMergeRequeue
from apps.users.models_identity import MergeActionKind, MergeResumeMode
from apps.users.security.reconciliation_approval import (
    ReconciliationApproval,
    ReconciliationApprovalVerifier,
    canonical_action_digest,
    decode_signature,
)


class Command(BaseCommand):
    help = "Create one signed, append-only Identity merge requeue request."

    def add_arguments(self, parser):
        parser.add_argument("--hold-block", required=True)
        parser.add_argument("--fresh-action-digest", required=True)
        parser.add_argument("--action-kind", required=True, choices=MergeActionKind.values)
        parser.add_argument("--resume-mode", required=True, choices=MergeResumeMode.values)
        parser.add_argument("--approval-reference", required=True)
        parser.add_argument("--approval-key-id")
        parser.add_argument("--approval-signature")
        parser.add_argument("--request-id", type=uuid.UUID, required=True)
        parser.add_argument("--correlation-id", type=uuid.UUID, required=True)
        parser.add_argument("--expires-at", required=True)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--execute", action="store_true")

    def handle(self, *args, **options):
        try:
            block = IdentityMergeHoldBlock.objects.select_related("operation").get(pk=options["hold_block"])
            expires_at = datetime.fromisoformat(options["expires_at"].replace("Z", "+00:00"))
        except (IdentityMergeHoldBlock.DoesNotExist, ValueError) as exc:
            raise CommandError("INVALID_REQUEUE_INPUT") from exc
        action = {
            "schema_version": 1,
            "purpose": "identity-merge-requeue",
            "environment": settings.ENVIRONMENT,
            "operation_id": str(block.operation_id),
            "hold_block_id": str(block.id),
            "prior_action_digest": block.action_digest,
            "fresh_action_digest": options["fresh_action_digest"],
            "action_kind": options["action_kind"],
            "resume_mode": options["resume_mode"],
            "expected_operation_version": block.operation.version,
            "request_id": str(options["request_id"]),
            "correlation_id": str(options["correlation_id"]),
            "expires_at": expires_at.isoformat(),
            "approval_reference": options["approval_reference"],
        }
        digest = canonical_action_digest(action)
        if not options["execute"]:
            self.stdout.write(digest)
            return
        if not options["approval_key_id"] or not options["approval_signature"]:
            raise CommandError("SIGNED_APPROVAL_REQUIRED")
        if expires_at <= timezone.now():
            raise CommandError("INVALID_RELEASE_EVIDENCE")
        approval = ReconciliationApproval(
            reference=options["approval_reference"],
            key_id=options["approval_key_id"],
            signature=options["approval_signature"],
        )
        if not ReconciliationApprovalVerifier.verify(approval, action):
            raise CommandError("INVALID_RELEASE_EVIDENCE")
        requeue, _created = IdentityMergeRequeue.objects.get_or_create(
            hold_block=block,
            fresh_action_digest=options["fresh_action_digest"],
            defaults={
                "operation": block.operation,
                "prior_action_digest": block.action_digest,
                "action_kind": options["action_kind"],
                "resume_mode": options["resume_mode"],
                "action_schema_version": 1,
                "environment": settings.ENVIRONMENT,
                "execution_build_digest": getattr(settings, "BUILD_COMMIT", ""),
                "approval_reference": options["approval_reference"],
                "key_id": options["approval_key_id"],
                "issued_at": timezone.now(),
                "expires_at": expires_at,
                "signature": decode_signature(options["approval_signature"]),
                "expected_operation_version": block.operation.version,
                "requested_by": options["approval_reference"],
                "request_id": options["request_id"],
                "correlation_id": options["correlation_id"],
            },
        )
        self.stdout.write(str(requeue.id))
