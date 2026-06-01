"""CQRS Query Side — read-only, no writes, replica-routed."""
import uuid
from typing import Optional

from apps.common.security.pii_vault import PiiEncryptionVault
from apps.common.tenancy.context_core import get_tenant_id
from apps.contragents.models.contragent import Contragent, PersonProfile


def _contragent_qs():
    tenant_id = get_tenant_id()
    return Contragent.objects.filter(tenant_id=tenant_id, is_deleted=False)


def get_contragents_page(
    cursor: Optional[uuid.UUID] = None,
    limit: int = 20,
    contragent_type: Optional[str] = None,
    inn: Optional[str] = None,
) -> dict:
    qs = _contragent_qs().order_by("-id")

    if cursor:
        qs = qs.filter(id__lt=cursor)
    if contragent_type:
        qs = qs.filter(contragent_type=contragent_type)
    if inn:
        qs = qs.filter(inn=inn)

    rows = list(qs[: limit + 1])
    has_next = len(rows) > limit
    rows = rows[:limit]

    return {
        "results": [_contragent_to_dict(c) for c in rows],
        "next_cursor": str(rows[-1].id) if has_next and rows else None,
        "count": len(rows),
    }


def get_contragent_detail(contragent_id: uuid.UUID) -> dict:
    contragent = _contragent_qs().filter(id=contragent_id).first()
    if not contragent:
        return None
    return _contragent_to_dict(contragent)


def get_person_profiles(contragent_id: uuid.UUID) -> list[dict]:
    tenant_id = get_tenant_id()
    profiles = PersonProfile.objects.filter(
        tenant_id=tenant_id,
        contragent_id=contragent_id,
        is_deleted=False,
    ).order_by("-id")
    return [_person_to_dict(p) for p in profiles]


def _contragent_to_dict(c: Contragent) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "inn": c.inn,
        "contragent_type": c.contragent_type,
    }


def _person_to_dict(p: PersonProfile) -> dict:
    try:
        passport_raw = PiiEncryptionVault.decrypt_field(p.encrypted_passport)
        phone_raw = PiiEncryptionVault.decrypt_field(p.encrypted_phone)
    except Exception:
        passport_raw = ""
        phone_raw = ""

    return {
        "id": p.id,
        "first_name": p.first_name,
        "last_name": p.last_name,
        "middle_name": p.middle_name,
        "masked_passport": passport_raw,
        "masked_phone": phone_raw,
    }
