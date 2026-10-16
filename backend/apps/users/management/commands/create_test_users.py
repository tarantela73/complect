"""Provision disposable local test users through the operator boundary."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.users.services.local_files_provisioning import provision_local_test_users


class Command(BaseCommand):
    help = "Provision fixed local/test RBAC users (idempotent)"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--database", required=True, help="Expected managed local database name")

    def handle(self, *args, **options) -> None:
        try:
            created_count, existing_count = provision_local_test_users(expected_database=options["database"])
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        except Exception as exc:
            raise CommandError("LOCAL_TEST_USER_PROVISIONING_FAILED") from exc
        self.stdout.write(self.style.SUCCESS(f"\nDone. Created: {created_count}, Existing: {existing_count}"))
