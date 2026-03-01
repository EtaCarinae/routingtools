from __future__ import annotations

import asyncio
import time

import httpx

from routetools.address_cleaner.geocoding.base import Geocoder
from routetools.address_cleaner.models import AddressType, GeocodeResult, GeocodeSource

_NOMINATIM_BASE = "https://nominatim.openstreetmap.org/search"

_OSM_TYPE_MAP: dict[str, AddressType] = {
    "house": AddressType.house,
    "building": AddressType.house,
    "road": AddressType.road,
    "residential": AddressType.road,
    "suburb": AddressType.suburb,
    "neighbourhood": AddressType.suburb,
    "city": AddressType.city,
    "town": AddressType.city,
    "village": AddressType.city,
    "municipality": AddressType.city,
}


def _parse_address_type(result: dict) -> AddressType:
    osm_type = result.get("type", "")
    osm_class = result.get("class", "")
    for key in (osm_type, osm_class):
        if key in _OSM_TYPE_MAP:
            return _OSM_TYPE_MAP[key]
    return AddressType.unknown


def _bbox_area(bbox: list[str] | None) -> float | None:
    if not bbox or len(bbox) < 4:
        return None
    try:
        south, north, west, east = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
        return abs((north - south) * (east - west))
    except (ValueError, TypeError):
        return None


def _has_house_number(result: dict) -> bool:
    address = result.get("address", {})
    return bool(address.get("house_number"))


class NominatimGeocoder(Geocoder):
    def __init__(
        self,
        user_agent: str,
        country_codes: str = "es",
        rps: float = 1.0,
    ) -> None:
        self._user_agent = user_agent
        self._country_codes = country_codes
        self._min_interval = 1.0 / rps
        self._lock = asyncio.Lock()
        self._last_request_time: float = 0.0

    async def _rate_limit(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_time = time.monotonic()

    async def geocode(self, address: str) -> GeocodeResult:
        await self._rate_limit()

        params = {
            "q": address,
            "format": "jsonv2",
            "countrycodes": self._country_codes,
            "addressdetails": "1",
            "limit": "3",
        }
        headers = {"User-Agent": self._user_agent}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(_NOMINATIM_BASE, params=params, headers=headers)
                response.raise_for_status()
                candidates: list[dict] = response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise
            return GeocodeResult(source=GeocodeSource.failed, candidate_count=0)
        except httpx.TimeoutException:
            raise
        except Exception:
            return GeocodeResult(source=GeocodeSource.failed, candidate_count=0)

        if not candidates:
            return GeocodeResult(source=GeocodeSource.failed, candidate_count=0)

        best = candidates[0]
        return GeocodeResult(
            formatted_address=best.get("display_name"),
            lat=float(best["lat"]) if "lat" in best else None,
            lon=float(best["lon"]) if "lon" in best else None,
            source=GeocodeSource.nominatim,
            importance=float(best.get("importance", 0.0)),
            address_type=_parse_address_type(best),
            bbox_area_deg2=_bbox_area(best.get("boundingbox")),
            house_number_resolved=_has_house_number(best),
            candidate_count=len(candidates),
            raw_response=best,
        )
