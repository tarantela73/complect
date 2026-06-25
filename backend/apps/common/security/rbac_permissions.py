"""
Canonical permission registry for ERP «Комплект».

Every permission used in @require_permission decorators must be declared here.
Format: "<resource>.<action>" — matches (action, resource) args of require_permission().

This module is the authoritative source for:
  - Seed data migration (initial roles/permissions)
  - Admin UI for role management
  - Regression guard in test_endpoint_coverage.py
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Permission:
    resource: str
    action: str

    @property
    def codename(self) -> str:
        return f"{self.resource}.{self.action}"

    def __str__(self) -> str:
        return self.codename


PERMISSIONS: tuple[Permission, ...] = (
    # contracts
    Permission("contracts", "read"),
    Permission("contracts", "create"),
    Permission("contracts", "write"),
    # finance
    Permission("finance", "read"),
    Permission("finance", "create"),
    Permission("finance", "write"),
    # objects
    Permission("objects", "read"),
    Permission("objects", "create"),
    Permission("objects", "write"),
    Permission("objects", "delete"),
    # contragents
    Permission("contragents", "read"),
    Permission("contragents", "create"),
    Permission("contragents", "write"),
    Permission("contragents", "delete"),
    # users (auth endpoints — not yet decorated, reserved for future)
    Permission("users", "read"),
    Permission("users", "create"),
    Permission("users", "write"),
    # files
    Permission("files", "read"),
    Permission("files", "create"),
    Permission("files", "delete"),
    Permission("files", "download"),
    # dlq (reserved for ops/admin)
    Permission("dlq", "read"),
    Permission("dlq", "write"),
    # dashboard (overview summary — all authenticated roles)
    Permission("dashboard", "read"),
    # director_dashboard (KPI screen — director + admin only)
    Permission("director_dashboard", "read"),
    # notifications (in-app notification feed — all authenticated roles)
    Permission("notifications", "read"),
    # ops (platform runtime observability — platform admin only)
    Permission("ops", "read"),
    # webhooks (integration endpoints)
    Permission("webhooks", "read"),
    Permission("webhooks", "write"),
    # payments (bank statement import)
    Permission("payments", "read"),
    Permission("payments", "write"),
    # requests (lead form / CRM)
    Permission("requests", "read"),
    Permission("requests", "write"),
    # cms (public site content management)
    Permission("cms", "read"),
    Permission("cms", "write"),
    Permission("cms", "publish"),
    # tasks (assignments module — sprint C)
    Permission("tasks", "read"),
    Permission("tasks", "write"),
    Permission("tasks", "delete"),
)

# Lookup by (action, resource) — matches require_permission() call signature
_BY_PAIR: dict[tuple[str, str], Permission] = {
    (p.action, p.resource): p for p in PERMISSIONS
}


def get_permission(action: str, resource: str) -> Permission:
    key = (action, resource)
    if key not in _BY_PAIR:
        raise KeyError(f"Unknown permission: action={action!r} resource={resource!r}")
    return _BY_PAIR[key]


def all_codenames() -> list[str]:
    return [p.codename for p in PERMISSIONS]
