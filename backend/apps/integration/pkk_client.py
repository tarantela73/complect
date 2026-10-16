from dataclasses import dataclass
from typing import Any

import httpx

_PKK_FEATURES_URL = "https://pkk.rosreestr.ru/api/features/1"
_TIMEOUT = 3.0


@dataclass(slots=True, frozen=True)
class CadastreResult:
    center_lat: float
    center_lon: float
    geometry: dict[str, Any] | None


async def search_cadastre(q: str) -> CadastreResult | None:
    if not q:
        return None

    params: dict[str, str | int] = {"text": q, "limit": 1}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            resp = await client.get(_PKK_FEATURES_URL, params=params)
            if not resp.is_success:
                return None
            data = resp.json()
    except Exception:
        return None

    features = data.get("features") or []
    if not features:
        return None

    feature = features[0]
    attrs = feature.get("attrs") or feature.get("properties") or {}
    geometry = feature.get("geometry")

    # PKK returns center as x/y in attrs
    center_x: str | float | None = attrs.get("center", {}).get("x") if isinstance(attrs.get("center"), dict) else None
    center_y: str | float | None = attrs.get("center", {}).get("y") if isinstance(attrs.get("center"), dict) else None

    if center_x is None or center_y is None:
        # Try to derive centroid from geometry bbox
        if geometry and geometry.get("type") in ("Polygon", "MultiPolygon"):
            coords = _flatten_coords(geometry)
            if coords:
                center_x = sum(c[0] for c in coords) / len(coords)
                center_y = sum(c[1] for c in coords) / len(coords)
        else:
            return None


    if center_x is None or center_y is None:
        return None

    return CadastreResult(
        center_lat=float(center_y),
        center_lon=float(center_x),
        geometry=geometry,
    )


def _flatten_coords(geometry: dict[str, Any]) -> list[tuple[float, float]]:
    geo_type = geometry.get("type", "")
    coords_raw = geometry.get("coordinates", [])
    result: list[tuple[float, float]] = []

    if geo_type == "Polygon":
        for ring in coords_raw:
            result.extend(ring)
    elif geo_type == "MultiPolygon":
        for polygon in coords_raw:
            for ring in polygon:
                result.extend(ring)

    return [(float(c[0]), float(c[1])) for c in result if len(c) >= 2]
