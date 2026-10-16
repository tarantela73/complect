"""Provision one disposable Files account and a private E2E credential file."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.users.services.local_files_provisioning import local_operator_scope, provision_local_files_account


class Command(BaseCommand):
    help = "Provision one local Files account using an owner-only credentials file"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--database", required=True)
        parser.add_argument("--credentials-file", required=True)

    def handle(self, *args, **options) -> None:
        path = Path(options["credentials_file"])
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
            raise CommandError("LOCAL_PRIVATE_CREDENTIAL_FILE_REQUIRED")
        values = path.read_text().splitlines()
        parsed = dict(line.split("=", 1) for line in values if "=" in line)
        password = parsed.get("E2E_PASSWORD") or secrets.token_urlsafe(24)
        if len(password) < 20:
            raise CommandError("LOCAL_PASSWORD_LENGTH")
        if "E2E_PASSWORD" not in parsed:
            content = path.read_text()
            separator = "" if content.endswith("\n") else "\n"
            path.write_text(content + separator + f"E2E_PASSWORD={password}\n")
            path.chmod(0o600)
        try:
            operator_ref = f"local-cli:uid:{os.getuid()}"
            with local_operator_scope(operator_ref):
                result = provision_local_files_account(
                    expected_database=options["database"],
                    email="local.tester@example.test",
                    password=password,
                    operator_ref=operator_ref,
                )
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        except Exception as exc:
            raise CommandError("LOCAL_TEST_USER_PROVISIONING_FAILED") from exc
        self.stdout.write(self.style.SUCCESS(f"Provisioned local account {result.email}"))
