from __future__ import annotations

import httpx

from routetools.address_cleaner.geocoding.base import Geocoder
from routetools.address_cleaner.models import AddressType, GeocodeResult, GeocodeSource

_GEOCODE_URL = "https://api.opencagedata.com/geocode/v1/json"

# OpenCage confidence is 0–10; map to AddressType by component presence
_CONFIDENCE_TYPE: list[tuple[int, AddressType]] = [
    (9, AddressType.house),   # 9–10: street-number precision
    (7, AddressType.road),    # 7–8: road level
    (5, AddressType.suburb),  # 5–6: suburb/neighbourhood
    (1, AddressType.city),    # 1–4: city or coarser
]


def _parse_address_type(result: dict) -> AddressType:
    components = result.get("components", {})
    confidence = result.get("confidence", 0)

    # Prefer component-based detection
    if components.get("house_number") or components.get("house"):
        return AddressType.house
    if components.get("road") or components.get("street"):
        if confidence >= 7:
            return AddressType.road
    if components.get("suburb") or components.get("neighbourhood"):
        return AddressType.suburb
    if components.get("city") or components.get("town") or components.get("village"):
        return AddressType.city

    # Fall back to confidence bands
    for threshold, addr_type in _CONFIDENCE_TYPE:
        if confidence >= threshold:
            return addr_type
    return AddressType.unknown


def _has_house_number(result: dict) -> bool:
    components = result.get("components", {})
    return bool(components.get("house_number") or components.get("house"))


def _bbox_area(bounds: dict | None) -> float | None:
    if not bounds:
        return None
    try:
        ne = bounds["northeast"]
        sw = bounds["southwest"]
        return abs((ne["lat"] - sw["lat"]) * (ne["lng"] - sw["lng"]))
    except (KeyError, TypeError):
        return None


def _confidence_to_importance(confidence: int) -> float:
    """Normalise OpenCage confidence (0–10) to Nominatim-style importance (0.0–1.0)."""
    return min(confidence / 10.0, 1.0)


class OpenCageGeocoder(Geocoder):
    def __init__(self, api_key: str, country_code: str = "es") -> None:
        self._api_key = api_key
        self._country_code = country_code

    async def geocode(self, address: str) -> GeocodeResult:
        params = {
            "q": address,
            "key": self._api_key,
            "countrycode": self._country_code,
            "limit": "3",
            "no_annotations": "1",
            "language": "es",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(_GEOCODE_URL, params=params)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 402, 403):
                raise
            return GeocodeResult(source=GeocodeSource.failed, candidate_count=0)
        except httpx.TimeoutException:
            raise
        except Exception:
            return GeocodeResult(source=GeocodeSource.failed, candidate_count=0)

        results: list[dict] = data.get("results", [])
        if not results:
            return GeocodeResult(source=GeocodeSource.failed, candidate_count=0)

        best = results[0]
        geometry = best.get("geometry", {})
        bounds = best.get("bounds")
        confidence = best.get("confidence", 0)

        return GeocodeResult(
            formatted_address=best.get("formatted"),
            lat=geometry.get("lat"),
            lon=geometry.get("lng"),
            source=GeocodeSource.opencage,
            importance=_confidence_to_importance(confidence),
            address_type=_parse_address_type(best),
            bbox_area_deg2=_bbox_area(bounds),
            house_number_resolved=_has_house_number(best),
            candidate_count=len(results),
            raw_response=best,
        )
