"""
CQRS Query Side — read-only projections, no business logic, no writes.
Cursor pagination: WHERE id < cursor ORDER BY id DESC (ADR rule 4.11).
"""
import uuid
from typing import Optional

from django.contrib.postgres.search import SearchQuery, SearchRank

from apps.files.models.file_record import FileRecord
from apps.files.repositories.file_repo import FileRepository
from apps.files.schemas.file_dto import FileDetailResponse


def _to_dto(record: FileRecord) -> FileDetailResponse:
    return FileDetailResponse.model_validate(record)


def get_files_page(
    cursor: Optional[uuid.UUID] = None,
    limit: int = 20,
    status: Optional[str] = None,
    q: Optional[str] = None,
) -> dict:
    qs = FileRepository.get_queryset()

    if q:
        search_query = SearchQuery(q, config="pg_catalog.russian", search_type="plain")
        qs = qs.filter(search_vector=search_query).order_by(
            SearchRank("search_vector", search_query).desc(), "-id"
        )
    else:
        qs = qs.order_by("-id")

    if cursor:
        qs = qs.filter(id__lt=cursor)

    if status:
        qs = qs.filter(status=status)

    rows = list(qs[: limit + 1])
    has_next = len(rows) > limit
    rows = rows[:limit]

    next_cursor = str(rows[-1].id) if has_next and rows else None

    return {
        "results": [_to_dto(r) for r in rows],
        "next_cursor": next_cursor,
        "count": len(rows),
    }
