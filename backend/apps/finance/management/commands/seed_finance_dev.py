"""
Seed finance transactions for local dev/QA.

Usage:
    python manage.py seed_finance_dev
    python manage.py seed_finance_dev --tenant-id <uuid>
    python manage.py seed_finance_dev --clear

Creates 5 transactions across different states and doc types so that
ManualMatchDialog happy-path and auto-matching can be tested in the browser:
  - 2 PENDING  transactions (exact doc_number match available)
  - 2 PENDING  transactions (amount + date match available)
  - 1 POSTED   transaction  (amount + INN match)
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from apps.finance.models.transaction import Transaction, TransactionStatus, TransactionType

_DEV_TENANT = uuid.UUID("a1b2c3d4-e5f6-4a7b-8c9d-e0f1a2b3c4d5")
_TODAY = date.today()

_SEED = [
    # Strategy 1 candidates — exact external_ref match with doc_number in bank line
    dict(
        external_ref="DOC-2025-001",
        amount=1_500_000,       # 15 000.00 руб
        counterparty_inn="7700000001",
        value_date=_TODAY - timedelta(days=1),
        transaction_type=TransactionType.PAYMENT,
        status=TransactionStatus.PENDING,
    ),
    dict(
        external_ref="DOC-2025-002",
        amount=3_200_000,       # 32 000.00 руб
        counterparty_inn="7700000002",
        value_date=_TODAY,
        transaction_type=TransactionType.PAYMENT,
        status=TransactionStatus.PENDING,
    ),
    # Strategy 2 candidates — amount + date window match
    dict(
        external_ref="",
        amount=7_500_000,       # 75 000.00 руб
        counterparty_inn="5000000001",
        value_date=_TODAY - timedelta(days=2),
        transaction_type=TransactionType.PAYMENT,
        status=TransactionStatus.PENDING,
    ),
    dict(
        external_ref="",
        amount=12_000_000,      # 120 000.00 руб
        counterparty_inn="5000000002",
        value_date=_TODAY - timedelta(days=1),
        transaction_type=TransactionType.PAYMENT,
        status=TransactionStatus.PENDING,
    ),
    # Strategy 3 candidate — amount + INN (POSTED, no external_ref, no easy date match)
    dict(
        external_ref="",
        amount=45_000_000,      # 450 000.00 руб
        counterparty_inn="9900000099",
        value_date=_TODAY - timedelta(days=30),
        transaction_type=TransactionType.PAYMENT,
        status=TransactionStatus.POSTED,
    ),
]


class Command(BaseCommand):
    help = "Seed finance transactions for dev/QA environment"

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-id",
            type=uuid.UUID,
            default=_DEV_TENANT,
            help=f"Tenant UUID (default: {_DEV_TENANT})",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all dev seed transactions for this tenant before seeding",
        )

    def handle(self, *args, **options):
        tenant_id: uuid.UUID = options["tenant_id"]

        if options["clear"]:
            deleted, _ = Transaction.objects.filter(tenant_id=tenant_id).delete()
            self.stdout.write(self.style.WARNING(
                f"Deleted {deleted} transactions for tenant {tenant_id}"
            ))

        created = 0
        for spec in _SEED:
            # Idempotent: skip if external_ref already exists for this tenant
            if spec["external_ref"] and Transaction.objects.filter(
                tenant_id=tenant_id,
                external_ref=spec["external_ref"],
            ).exists():
                self.stdout.write(f"  skip (exists): {spec['external_ref']}")
                continue

            contract_id = uuid.uuid4()
            Transaction.objects.create(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                contract_id=contract_id,
                transaction_type=spec["transaction_type"],
                status=spec["status"],
                amount=spec["amount"],
                external_ref=spec["external_ref"],
                value_date=spec["value_date"],
                counterparty_inn=spec["counterparty_inn"],
            )
            created += 1
            label = spec["external_ref"] or f"amount={spec['amount']} inn={spec['counterparty_inn']}"
            self.stdout.write(f"  created: {label}")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone: {created} transaction(s) created for tenant {tenant_id}"
        ))
