import hashlib
import json
import logging
import os
import stat
import uuid
from pathlib import Path

from django.conf import settings
from django.core.management import BaseCommand, CommandError

from apps.users.repositories.identity_collision_repository import IdentityCollisionRepository
from apps.users.services.identity_foundation import classify_collision_candidates, serialize_groups
from apps.users.services.pii_access_audit_service import PiiAccessAuditService, PiiAuditUnavailable

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Write the protected Release-N normalized-email reconciliation report."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True)
        parser.add_argument("--fail-on-duplicates", action="store_true")
        parser.add_argument("--fail-on-invalid-candidates", action="store_true")
        parser.add_argument("--actor-type", required=True, choices=["operator", "system", "user"])
        parser.add_argument("--actor-ref", required=True)
        parser.add_argument("--request-id", required=True, type=uuid.UUID)
        parser.add_argument("--correlation-id", required=True, type=uuid.UUID)

    def handle(self, *args, **options):
        output_path = Path(options["output"])
        repository = Path(settings.BASE_DIR).resolve()
        requested_parent = output_path.parent.absolute()
        try:
            protected_parent = output_path.parent.resolve(strict=True)
        except OSError as exc:
            raise CommandError("Protected report directory must already exist") from exc
        if protected_parent != requested_parent:
            raise CommandError("Protected report directory must not contain symlinks")
        if protected_parent.is_relative_to(repository):
            raise CommandError("Reconciliation output must be outside the repository")

        dir_fd = os.open(protected_parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        temp_name = None
        try:
            directory_stat = os.fstat(dir_fd)
            if directory_stat.st_uid != os.geteuid() or stat.S_IMODE(directory_stat.st_mode) & 0o077:
                raise CommandError("Protected report directory must be owner-only")
            try:
                target_stat = os.stat(output_path.name, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if not stat.S_ISREG(target_stat.st_mode):
                    raise CommandError("Report target is a symlink or non-regular file")

            subjects = IdentityCollisionRepository.enumerate_subjects()
            try:
                PiiAccessAuditService.append_batch_and_commit(
                    subjects=subjects,
                    actor_type=options["actor_type"],
                    actor_ref=options["actor_ref"],
                    reason_code="IDENTITY_EMAIL_RECONCILIATION_REPORT",
                    request_id=options["request_id"],
                    correlation_id=options["correlation_id"],
                )
            except PiiAuditUnavailable as exc:
                raise CommandError("PII audit unavailable") from exc

            candidates = IdentityCollisionRepository.load_pii_candidates(
                [subject.user_pk for subject in subjects],
            )
            resolved_source_pks = IdentityCollisionRepository.resolved_deferred_source_user_pks()
            candidates = [candidate for candidate in candidates if candidate.user_pk not in resolved_source_pks]
            groups, invalid = classify_collision_candidates(candidates)
            payload = {
                "schema_version": 1,
                "unresolved_duplicate_groups": len(groups),
                "groups": serialize_groups(groups),
                "invalid_candidates": {
                    "count": len(invalid),
                    "rows": [
                        {
                            "user_pk": row.user_pk,
                            "identity_id": str(row.identity_id) if row.identity_id else None,
                            "error_code": row.error_code,
                        }
                        for row in invalid
                    ],
                },
            }
            encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode()
            temp_name = f".{output_path.name}.{uuid.uuid4().hex}.tmp"
            temp_fd = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=dir_fd,
            )
            with os.fdopen(temp_fd, "wb", closefd=True) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, output_path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            temp_name = None
            os.fsync(dir_fd)
        finally:
            if temp_name is not None:
                try:
                    os.unlink(temp_name, dir_fd=dir_fd)
                except FileNotFoundError:
                    pass
            os.close(dir_fd)

        digest = hashlib.sha256(encoded).hexdigest()
        logger.info(
            "identity_email_reconciliation_reported duplicate_groups=%d invalid_candidates=%d report_sha256=%s",
            len(groups),
            len(invalid),
            digest,
        )
        self.stdout.write(
            f"duplicate_groups={len(groups)} invalid_candidates={len(invalid)} sha256={digest}",
        )
        if groups and options["fail_on_duplicates"]:
            raise CommandError(f"{len(groups)} unresolved normalized-email groups")
        if invalid and options["fail_on_invalid_candidates"]:
            raise CommandError(f"{len(invalid)} invalid identity candidates")
