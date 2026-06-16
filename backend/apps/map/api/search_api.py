import asyncio

from django.http import HttpRequest
from ninja import Query, Router

from apps.common.api.permissions import require_permission
from apps.integration.dadata_client import geolocate_address, suggest_address
from apps.integration.pkk_client import search_cadastre
from apps.map.schemas.search_dto import (
    MapAddressResult,
    MapCadastreResult,
    MapObjectResult,
    MapReverseResult,
    MapSearchResponse,
)
from apps.objects.queries.map_search import search_objects_for_map

router = Router(tags=["map"])


@router.get("/search", response={200: MapSearchResponse, 403: dict})
@require_permission("read", "objects")
async def map_search(
    request: HttpRequest,
    q: str = Query(min_length=2, max_length=200),
):
    tenant_id = getattr(request, "jwt_tenant_id", None)

    objects_task = asyncio.to_thread(search_objects_for_map, q, tenant_id, 5)
    addresses_task = suggest_address(q, limit=5)
    cadastre_task = search_cadastre(q)

    objects_raw, addresses_raw, cadastre_raw = await asyncio.gather(
        objects_task,
        addresses_task,
        cadastre_task,
        return_exceptions=True,
    )

    objects: list[MapObjectResult] = []
    if isinstance(objects_raw, list):
        for obj in objects_raw:
            objects.append(
                MapObjectResult(
                    id=obj.id,
                    name=obj.name,
                    address=obj.address,
                    lat=obj.lat,
                    lon=obj.lon,
                    status=obj.status,
                )
            )

    addresses: list[MapAddressResult] = []
    if isinstance(addresses_raw, list):
        for addr in addresses_raw:
            addresses.append(
                MapAddressResult(
                    label=addr.label,
                    lat=addr.lat,
                    lon=addr.lon,
                )
            )

    cadastre: list[MapCadastreResult] = []
    if cadastre_raw and not isinstance(cadastre_raw, BaseException):
        cadastre.append(
            MapCadastreResult(
                label=q,
                center_lat=cadastre_raw.center_lat,
                center_lon=cadastre_raw.center_lon,
                geometry=cadastre_raw.geometry,
            )
        )

    return 200, MapSearchResponse(objects=objects, addresses=addresses, cadastre=cadastre)


@router.get("/reverse", response={200: MapReverseResult, 403: dict})
@require_permission("read", "objects")
async def map_reverse(
    request: HttpRequest,
    lat: float = Query(...),
    lon: float = Query(...),
):
    result = await geolocate_address(lat, lon)
    if result is None:
        return 200, MapReverseResult(label=None, lat=lat, lon=lon)
    return 200, MapReverseResult(label=result.label, lat=result.lat, lon=result.lon)
