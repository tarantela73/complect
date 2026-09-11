"""Global append-only evidence access. The latch is its sole mutable field."""

from uuid import UUID

from django.db.models import Q, Subquery

from apps.users.models import IdentitySecurityAuditRecord, IdentitySession
from apps.users.security.identity_audit_hold import AuditHoldBindingChangedError, AuditHoldBindingError


class IdentityAuditHoldRepository:
    @staticmethod
    def discover(audit_ids: tuple[UUID, ...], *, using: str):
        records = tuple(
            IdentitySecurityAuditRecord.objects.using(using).filter(id__in=audit_ids).order_by("id"),
        )
        if len(records) != len(audit_ids):
            raise AuditHoldBindingError("AUDIT_HOLD_RECORD_MISSING")
        sessions = dict(
            IdentitySession.objects.using(using)
            .filter(
                id__in=[row.session_id for row in records if row.session_id],
            )
            .values_list("id", "identity_id")
        )
        identities = set()
        for row in records:
            identity = row.identity_id
            if row.session_id is not None:
                resolved = sessions.get(row.session_id)
                if resolved is None:
                    raise AuditHoldBindingError("AUDIT_HOLD_SESSION_MISSING")
                if identity is not None and identity != resolved:
                    raise AuditHoldBindingError("AUDIT_HOLD_REFERENCE_MISMATCH")
                identity = resolved if identity is None else identity
            if identity is None:
                raise AuditHoldBindingError("AUDIT_HOLD_IDENTITY_UNRESOLVED")
            identities.add(identity)
        return records, sessions, identities

    @staticmethod
    def latch(records, sessions, *, using: str) -> int:
        locked_sessions = dict(
            IdentitySession.objects.using(using)
            .select_for_update()
            .filter(id__in=sessions)
            .order_by("id")
            .values_list("id", "identity_id"),
        )
        if locked_sessions != sessions:
            raise AuditHoldBindingChangedError("AUDIT_HOLD_BINDING_CHANGED")
        locked_records = tuple(
            IdentitySecurityAuditRecord.objects.using(using)
            .select_for_update()
            .filter(id__in=[row.id for row in records])
            .order_by("id"),
        )
        if [(r.id, r.identity_id, r.session_id) for r in locked_records] != [
            (r.id, r.identity_id, r.session_id) for r in records
        ]:
            raise AuditHoldBindingChangedError("AUDIT_HOLD_TARGET_CHANGED")
        unheld = [row.id for row in locked_records if not row.is_legal_hold]
        if not unheld:
            return 0
        return (
            IdentitySecurityAuditRecord.objects.using(using)
            .filter(
                id__in=unheld,
                is_legal_hold=False,
            )
            .update(is_legal_hold=True)
        )

    @staticmethod
    def held_identity_ids(identity_ids: tuple[UUID, ...], *, using: str) -> set[UUID]:
        """Fresh independent statement after all graph fences; no audit row locks."""
        sessions = IdentitySession.objects.using(using).filter(identity_id__in=identity_ids)
        records = (
            IdentitySecurityAuditRecord.objects.using(using)
            .filter(is_legal_hold=True)
            .filter(
                Q(identity_id__in=identity_ids)
                | Q(identity_id__isnull=True, session_id__in=Subquery(sessions.values("id"))),
            )
        )
        session_identities = dict(sessions.values_list("id", "identity_id"))
        return {
            identity or session_identities[session]
            for identity, session in records.values_list("identity_id", "session_id")
        }
