from __future__ import annotations

import uuid

import structlog

from apps.notifications.handlers.base import BaseNotificationHandler, NotificationContext

logger = structlog.get_logger(__name__)

_STATE_EMOJI = {"warn": "⚠", "crit": "✖", "ok": "✔", "recovering": "↻"}


class SLOStateChangedHandler(BaseNotificationHandler):
    event_type = "slo.state_changed"
    default_email_enabled = True
    default_in_app_enabled = True

    def get_recipient_ids(self, ctx: NotificationContext) -> list[uuid.UUID]:
        from apps.common.models.rbac import RoleBinding, RolePermission

        try:
            admin_role_ids = RolePermission.objects.filter(
                permission__resource="ops",
                permission__action="read",
            ).values_list("role_id", flat=True)

            user_ids = (
                RoleBinding.objects.filter(role_id__in=admin_role_ids, is_active=True)
                .values_list("user_id", flat=True)
                .distinct()
            )
            return [uuid.UUID(str(uid)) for uid in user_ids]
        except Exception as exc:
            logger.error("slo_recipient_lookup_failed", error=str(exc))
            return []

    def get_title(self, ctx: NotificationContext) -> str:
        new_state = ctx.payload.get("new_state", "")
        label = ctx.payload.get("slo_label", ctx.payload.get("slo_name", "SLO"))
        icon = _STATE_EMOJI.get(new_state, "")
        return f"{icon} SLO {new_state.upper()}: {label}"

    def get_body(self, ctx: NotificationContext) -> str:
        old = ctx.payload.get("old_state", "?")
        new = ctx.payload.get("new_state", "?")
        value = ctx.payload.get("current_value")
        warn = ctx.payload.get("warn_threshold")
        crit = ctx.payload.get("critical_threshold")
        budget = ctx.payload.get("error_budget", 1.0)
        budget_pct = round(float(budget) * 100, 1)
        val_str = f"{float(value):.1f}" if value is not None else "N/A"
        return (
            f"{old} → {new}. Значение: {val_str} "
            f"(warn ≥ {warn}, crit ≥ {crit}). "
            f"Error budget (30m): {budget_pct}% compliant."
        )
