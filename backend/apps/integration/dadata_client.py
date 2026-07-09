from dataclasses import dataclass

import httpx
from django.conf import settings

_DADATA_SUGGEST_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
_DADATA_GEOLOCATE_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/geolocate/address"
_TIMEOUT = 5.0


@dataclass(slots=True, frozen=True)
class AddressSuggestion:
    label: str
    lat: float
    lon: float


async def suggest_address(q: str, limit: int = 5) -> list[AddressSuggestion]:
    api_key = getattr(settings, "DADATA_API_KEY", "")
    if not q or not api_key:
        return []

    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"query": q, "count": limit}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_DADATA_SUGGEST_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []

    results: list[AddressSuggestion] = []
    for item in data.get("suggestions", []):
        geo_lat = item.get("data", {}).get("geo_lat")
        geo_lon = item.get("data", {}).get("geo_lon")
        if geo_lat and geo_lon:
            results.append(
                AddressSuggestion(
                    label=item.get("value", ""),
                    lat=float(geo_lat),
                    lon=float(geo_lon),
                )
            )
    return results


async def geolocate_address(lat: float, lon: float) -> AddressSuggestion | None:
    """Reverse geocoding: nearest address for the given coordinates, or None."""
    api_key = getattr(settings, "DADATA_API_KEY", "")
    if not api_key:
        return None

    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"lat": lat, "lon": lon, "count": 1}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_DADATA_GEOLOCATE_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None

    suggestions = data.get("suggestions", [])
    if not suggestions:
        return None

    item = suggestions[0]
    geo_lat = item.get("data", {}).get("geo_lat")
    geo_lon = item.get("data", {}).get("geo_lon")
    return AddressSuggestion(
        label=item.get("value", ""),
        lat=float(geo_lat) if geo_lat else lat,
        lon=float(geo_lon) if geo_lon else lon,
    )
