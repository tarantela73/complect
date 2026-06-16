from typing import Any, Optional

from ninja import Schema


class MapObjectResult(Schema):
    id: str
    name: str
    address: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    status: str


class MapAddressResult(Schema):
    label: str
    lat: float
    lon: float


class MapCadastreResult(Schema):
    label: str
    center_lat: float
    center_lon: float
    geometry: Optional[dict[str, Any]] = None


class MapSearchResponse(Schema):
    objects: list[MapObjectResult]
    addresses: list[MapAddressResult]
    cadastre: list[MapCadastreResult]


class MapReverseResult(Schema):
    label: Optional[str] = None
    lat: float
    lon: float
