"""CQRS Query Side — read-only, no writes, replica-routed."""

import uuid
from typing import Optional

from django.contrib.postgres.search import SearchQuery, SearchRank

from apps.common.tenancy.context_core import get_tenant_id
from apps.contragents.models.contragent import Contragent, PersonProfile


def get_contragent_count() -> int:
    return _contragent_qs().count()


def _contragent_qs():
    tenant_id = get_tenant_id()
    return Contragent.objects.filter(tenant_id=tenant_id, is_deleted=False)


def get_contragents_page(
    cursor: Optional[uuid.UUID] = None,
    limit: int = 20,
    contragent_type: Optional[str] = None,
    inn: Optional[str] = None,
    q: Optional[str] = None,
) -> dict:
    qs = _contragent_qs()

    if q:
        search_query = SearchQuery(q, config="pg_catalog.russian", search_type="plain")
        qs = qs.filter(search_vector=search_query).order_by(SearchRank("search_vector", search_query).desc(), "-id")
    else:
        qs = qs.order_by("-id")

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


def get_contragent_detail(contragent_id: uuid.UUID) -> dict | None:
    contragent = _contragent_qs().filter(id=contragent_id).first()
    if not contragent:
        return None
    return _contragent_to_dict(contragent)


def get_person_profile_ids(contragent_id: uuid.UUID, tenant_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        PersonProfile.objects.using("default").filter(
            tenant_id=tenant_id,
            contragent_id=contragent_id,
            is_deleted=False,
        ).values_list("id", flat=True)
    )


def get_person_profile_rows(
    contragent_id: uuid.UUID, tenant_id: uuid.UUID, profile_ids: list[uuid.UUID]
) -> list[PersonProfile]:
    profiles = PersonProfile.objects.using("default").filter(
        tenant_id=tenant_id,
        contragent_id=contragent_id,
        id__in=profile_ids,
        is_deleted=False,
    ).only(
        "id", "tenant_id", "first_name", "last_name", "middle_name",
        "encrypted_passport", "encrypted_phone",
    ).order_by("-id")
    return list(profiles)

def _contragent_to_dict(c: Contragent) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "inn": c.inn,
        "contragent_type": c.contragent_type,
        "created_at": c.created_at,
    }
