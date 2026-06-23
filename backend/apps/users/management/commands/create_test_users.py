"""
Management command: create_test_users

Creates 4 test users with fixed credentials and assigns RBAC roles
for the public-site tenant. Idempotent — safe to run multiple times.

Runs ONLY in DEBUG=True environments unless --force is passed.

Usage:
    python manage.py create_test_users          # dev only (DEBUG=True required)
    python manage.py create_test_users --force  # bypass env guard (CI, staging)
"""
from __future__ import annotations

import uuid

import structlog
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

PUBLIC_SITE_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

_TEST_USERS = [
    {
        "username": "dir_test",
        "password": "Test123!",
        "email": "dir_test@test.com",
        "role_name": "director",
    },
    {
        "username": "mgr_test",
        "password": "Test123!",
        "email": "mgr_test@test.com",
        "role_name": "manager",
    },
    {
        "username": "acc_test",
        "password": "Test123!",
        "email": "acc_test@test.com",
        "role_name": "accountant",
    },
    {
        "username": "foreman_test",
        "password": "Test123!",
        "email": "foreman_test@test.com",
        "role_name": "foreman",
    },
]

logger = structlog.get_logger(__name__)


class Command(BaseCommand):
    help = "Create test users with RBAC roles for Sprint C role validation (idempotent)"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow running outside DEBUG environments (use for CI/staging only)",
        )

    def handle(self, *args, **options) -> None:
        if not settings.DEBUG and not options.get("force"):
            raise CommandError(
                "create_test_users is disabled in non-DEBUG environments. "
                "Pass --force to override (CI/staging only)."
            )
        from apps.common.models.rbac import Role, RoleBinding
        from apps.users.models import User

        created_count = 0
        existing_count = 0
        roles_assigned_count = 0

        for spec in _TEST_USERS:
            user, created = User.objects.get_or_create(
                username=spec["username"],
                defaults={
                    "email": spec["email"],
                    "tenant_id": PUBLIC_SITE_TENANT_ID,
                },
            )

            if created:
                user.set_password(spec["password"])
                user.save(update_fields=["password"])
                created_count += 1
                self.stdout.write(f"  created: {spec['username']}")
                logger.info(
                    "test_user_created",
                    username=spec["username"],
                    tenant_id=str(PUBLIC_SITE_TENANT_ID),
                )
            else:
                existing_count += 1
                self.stdout.write(f"  exists:  {spec['username']}")

            role = Role.objects.filter(name=spec["role_name"]).first()
            if role is None:
                msg = (
                    f"  WARNING: role '{spec['role_name']}' not found in DB"
                    f" — skipping binding for {spec['username']}"
                )
                self.stdout.write(self.style.WARNING(msg))
                logger.warning(
                    "test_user_role_not_found",
                    username=spec["username"],
                    role_name=spec["role_name"],
                    tenant_id=str(PUBLIC_SITE_TENANT_ID),
                )
                continue

            _, binding_created = RoleBinding.objects.get_or_create(
                tenant_id=PUBLIC_SITE_TENANT_ID,
                user_id=user.pk,
                role=role,
                defaults={"is_active": True},
            )

            if binding_created:
                roles_assigned_count += 1
                self.stdout.write(f"  role assigned: {spec['username']} → {spec['role_name']}")
                logger.info(
                    "test_user_role_binding_created",
                    username=spec["username"],
                    role_name=spec["role_name"],
                    tenant_id=str(PUBLIC_SITE_TENANT_ID),
                    user_id=str(user.pk),
                )
            else:
                self.stdout.write(f"  role exists:   {spec['username']} → {spec['role_name']}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. Created: {created_count}, Existing: {existing_count}, Roles assigned: {roles_assigned_count}"
            )
        )
