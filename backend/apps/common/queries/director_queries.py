"""
CQRS Query Side — Director Dashboard KPI aggregation.
Re-exports domain-specific query functions. No direct cross-domain ORM imports (ADR-0005).
"""
from __future__ import annotations

from apps.contracts.queries.contract_queries import get_open_contracts_count
from apps.objects.queries.object_queries import get_objects_summary
from apps.payments.queries.payment_queries import get_payments_month_summary

__all__ = ["get_open_contracts_count", "get_objects_summary", "get_payments_month_summary"]
