import uuid
from typing import Optional

from apps.common.contracts.user_info_contracts import UserDisplayDTO, UserInfoProtocol
from apps.objects.models.object_stage import ObjectStage
from apps.objects.models.supply_object import SupplyObject
from apps.objects.repositories.object_repo import ObjectRepository
from apps.objects.schemas.object_dto import (
    FeatureCollectionSchema,
    FeatureSchema,
    ObjectStageResponse,
    PointGeometry,
    SupplyObjectDetailResponse,
    SupplyObjectProperties,
    UserDisplaySchema,
)


def _to_feature(obj: SupplyObject) -> FeatureSchema:
    geometry = None
    if obj.location:
        geometry = PointGeometry(coordinates=[obj.location.x, obj.location.y])
    return FeatureSchema(
        geometry=geometry,
        properties=SupplyObjectProperties(
            id=obj.id,
            name=obj.name,
            address=obj.address,
            object_type=obj.object_type,
            resource_type=obj.resource_type,
            status=obj.status,
            contract_id=obj.contract_id,
            contragent_id=obj.contragent_id,
        ),
    )


def _resolve_user(
    user_id: Optional[uuid.UUID],
    display_map: dict[uuid.UUID, UserDisplayDTO],
) -> Optional[UserDisplaySchema]:
    if user_id is None or user_id not in display_map:
        return None
    dto = display_map[user_id]
    return UserDisplaySchema(id=dto.user_id, full_name=dto.full_name)


def _to_detail(
    obj: SupplyObject,
    display_map: dict[uuid.UUID, UserDisplayDTO],
    stages: Optional[list[ObjectStage]] = None,
) -> SupplyObjectDetailResponse:
    stage_responses = []
    if stages is not None:
        stage_responses = [
            ObjectStageResponse(
                stage_key=s.stage_key,
                status=s.status,
                order=s.order,
                is_optional=s.is_optional,
                completed_at=s.completed_at,
                completed_by_id=s.completed_by_id,
                note=s.note,
            )
            for s in sorted(stages, key=lambda x: x.order)
        ]
    return SupplyObjectDetailResponse(
        id=obj.id,
        name=obj.name,
        address=obj.address,
        object_type=obj.object_type,
        resource_type=obj.resource_type,
        status=obj.status,
        description=obj.description,
        manager=_resolve_user(obj.manager_id, display_map),
        foreman=_resolve_user(obj.foreman_id, display_map),
        team_name=obj.team_name,
        contract_id=obj.contract_id,
        contragent_id=obj.contragent_id,
        planned_start_date=obj.planned_start_date,
        planned_end_date=obj.planned_end_date,
        actual_start_date=obj.actual_start_date,
        actual_end_date=obj.actual_end_date,
        stages=stage_responses,
        longitude=obj.location.x if obj.location else None,
        latitude=obj.location.y if obj.location else None,
    )


def get_objects_page(
    cursor: Optional[uuid.UUID] = None,
    limit: int = 20,
    object_type: Optional[str] = None,
    resource_type: Optional[str] = None,
    contract_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    manager_id: Optional[uuid.UUID] = None,
    bbox: Optional[tuple[float, float, float, float]] = None,
    user_info: Optional[UserInfoProtocol] = None,
    object_ids: Optional[list[uuid.UUID]] = None,
    q: Optional[str] = None,
) -> dict:
    qs = ObjectRepository.get_queryset().order_by("-id")

    if cursor:
        qs = qs.filter(id__lt=cursor)
    if object_type:
        qs = qs.filter(object_type=object_type)
    if resource_type:
        qs = qs.filter(resource_type=resource_type)
    if contract_id:
        qs = qs.filter(contract_id=contract_id)
    if status:
        qs = qs.filter(status=status)
    if manager_id:
        qs = qs.filter(manager_id=manager_id)
    if bbox:
        qs = ObjectRepository.bbox_filter(qs, bbox)
    if object_ids is not None:
        qs = qs.filter(id__in=object_ids)
    if q:
        qs = qs.filter(name__icontains=q)

    rows = list(qs[: limit + 1])
    has_next = len(rows) > limit
    rows = rows[:limit]

    next_cursor = str(rows[-1].id) if has_next and rows else None

    display_map: dict[uuid.UUID, UserDisplayDTO] = {}
    if user_info and rows:
        from apps.common.tenancy.context_core import get_tenant_id
        user_ids = list({uid for o in rows for uid in [o.manager_id, o.foreman_id] if uid})
        if user_ids:
            display_map = user_info.get_users_display(user_ids, get_tenant_id())

    return {
        "results": [_to_detail(o, display_map, stages=None) for o in rows],
        "next_cursor": next_cursor,
        "count": len(rows),
    }


def get_objects_geojson(
    contract_id: Optional[uuid.UUID] = None,
    bbox: Optional[tuple[float, float, float, float]] = None,
) -> FeatureCollectionSchema:
    qs = ObjectRepository.get_queryset()

    if contract_id:
        qs = qs.filter(contract_id=contract_id)
    if bbox:
        qs = ObjectRepository.bbox_filter(qs, bbox)

    return FeatureCollectionSchema(features=[_to_feature(o) for o in qs])


def get_object_detail(
    object_id: uuid.UUID,
    user_info: Optional[UserInfoProtocol] = None,
) -> SupplyObjectDetailResponse:
    obj = ObjectRepository.get_by_id(object_id)
    stages = list(ObjectStage.objects.filter(
        object_id=object_id, tenant_id=obj.tenant_id, is_deleted=False
    ).order_by("order"))

    display_map: dict[uuid.UUID, UserDisplayDTO] = {}
    if user_info:
        user_ids = [uid for uid in [obj.manager_id, obj.foreman_id] if uid]
        if user_ids:
            display_map = user_info.get_users_display(user_ids, obj.tenant_id)

    return _to_detail(obj, display_map, stages=stages)
