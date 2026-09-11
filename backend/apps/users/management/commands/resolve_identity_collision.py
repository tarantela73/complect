from django.core.management import BaseCommand, CommandError

from apps.users.models import IdentityMergeOperation, User
from apps.users.security.reconciliation_approval import (
    ReconciliationApproval,
    canonical_action_digest,
)
from apps.users.services.identity_collision_resolution import (
    IdentityCollisionResolutionError,
    IdentityCollisionResolutionService,
)


class Command(BaseCommand):
    help = "Dry-run or execute one signed normalized-email collision decision."

    def add_arguments(self, parser):
        modes = parser.add_mutually_exclusive_group(required=True)
        modes.add_argument("--correct-email", type=int, metavar="USER_PK")
        modes.add_argument("--merge", type=int, metavar="SOURCE_USER_PK")
        modes.add_argument("--cancel-merge", metavar="OPERATION_ID")
        parser.add_argument("--email")
        parser.add_argument("--into", type=int, metavar="CANONICAL_USER_PK")
        parser.add_argument("--approval-reference")
        parser.add_argument("--approval-key-id")
        parser.add_argument("--approval-signature")
        execution = parser.add_mutually_exclusive_group()
        execution.add_argument("--dry-run", action="store_true")
        execution.add_argument("--execute", action="store_true")

    def handle(self, *args, **options):
        if not options["approval_reference"]:
            raise CommandError("--approval-reference is required")
        service = IdentityCollisionResolutionService()
        reference = options["approval_reference"]
        try:
            if options["correct_email"] is not None:
                if not options["email"]:
                    raise CommandError("--email is required with --correct-email")
                user = User.objects.only("pk", "identity_id", "security_version").get(pk=options["correct_email"])
                action = service.build_correct_email_action(
                    user=user,
                    new_email=options["email"],
                    reference=reference,
                )
                def invoke(approval):
                    return service.correct_email(
                        user_pk=user.pk,
                        new_email=options["email"],
                        approval=approval,
                    )
            elif options["merge"] is not None:
                if options["into"] is None:
                    raise CommandError("--into is required with --merge")
                source = User.objects.only("pk", "identity_id", "security_version").get(pk=options["merge"])
                canonical = User.objects.only("pk", "identity_id", "security_version").get(pk=options["into"])
                action = service.build_merge_action(source=source, canonical=canonical, reference=reference)
                def invoke(approval):
                    return service.approve_merge(
                        source_pk=source.pk,
                        canonical_pk=canonical.pk,
                        approval=approval,
                    )
            else:
                operation = IdentityMergeOperation.objects.get(pk=options["cancel_merge"])
                action = service.build_cancel_action(operation=operation, reference=reference)
                def invoke(approval):
                    return service.cancel_merge(
                        operation_id=operation.id,
                        approval=approval,
                    )
        except (User.DoesNotExist, IdentityMergeOperation.DoesNotExist) as exc:
            raise CommandError("NOT_FOUND") from exc

        digest = canonical_action_digest(action)
        if not options["execute"]:
            self.stdout.write(digest)
            return
        if not options["approval_key_id"] or not options["approval_signature"]:
            raise CommandError("--approval-key-id and --approval-signature are required with --execute")
        approval = ReconciliationApproval(
            reference=reference,
            key_id=options["approval_key_id"],
            signature=options["approval_signature"],
        )
        try:
            result = invoke(approval)
        except IdentityCollisionResolutionError as exc:
            raise CommandError(exc.code.value) from exc
        self.stdout.write(str(result.pk))
