from __future__ import annotations

import asyncio
import time

import httpx

from routetools.address_cleaner.errors import ProviderAuthError
from routetools.address_cleaner.geocoding.base import Geocoder, in_locality, pick_candidate
from routetools.address_cleaner.models import AddressType, GeocodeResult, GeocodeSource
from routetools.address_cleaner.normalizer import fold

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

_LOCALITY_KEYS = ("city", "town", "village", "municipality", "hamlet")


def _parse_address_type(result: dict) -> AddressType:
    # A resolved house number is the strongest signal: house-level hits are often
    # POIs (shop, hotel…) whose type says nothing about precision.
    if _has_house_number(result):
        return AddressType.house
    # jsonv2 uses "category" where the plain json format uses "class".
    for key in ("addresstype", "type", "category", "class"):
        value = result.get(key, "")
        if value in _OSM_TYPE_MAP:
            return _OSM_TYPE_MAP[value]
    # Any street-like way (pedestrian, primary, square…) — OSM ranks some of them 25
    if "highway" in (result.get("category"), result.get("class")):
        return AddressType.road
    rank = result.get("place_rank")
    if isinstance(rank, int):
        if rank >= 28:
            return AddressType.house
        if rank >= 26:
            return AddressType.road
        if rank >= 20:
            return AddressType.suburb
        return AddressType.city
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


def _locality(result: dict) -> str | None:
    address = result.get("address", {})
    return next((address[k] for k in _LOCALITY_KEYS if address.get(k)), None)


def _coords(result: dict) -> tuple[float | None, float | None]:
    try:
        return float(result["lat"]), float(result["lon"])
    except (KeyError, TypeError, ValueError):
        return None, None


def _street_part(address: str) -> str:
    """The address without its trailing locality: "calle colón 1, valencia" → "calle colón 1"."""
    return address.rsplit(",", 1)[0].strip()


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
        # folded locality → "west,north,east,south" (None if not found); one lookup per run
        self._viewboxes: dict[str, str | None] = {}

    async def _rate_limit(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_time = time.monotonic()

    async def _search(self, params: dict[str, str]) -> list[dict] | None:
        """Raw candidates, [] if none, None on a soft failure (timeout, 5xx…)."""
        await self._rate_limit()
        params = {
            "format": "jsonv2",
            "countrycodes": self._country_codes,
            "addressdetails": "1",
            **params,
        }
        headers = {"User-Agent": self._user_agent}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(_NOMINATIM_BASE, params=params, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise ProviderAuthError(
                    f"Nominatim rejected the request (HTTP {exc.response.status_code}). "
                    "Check NOMINATIM_USER_AGENT: it must identify you with a real contact."
                ) from None
            return None
        except Exception:
            return None

    async def _locality_viewbox(self, locality: str) -> str | None:
        key = fold(locality)
        if key not in self._viewboxes:
            found = await self._search({"city": locality, "limit": "1"})
            viewbox = None
            if found and found[0].get("addresstype") in ("city", "town", "village", "municipality"):
                south, north, west, east = found[0]["boundingbox"]
                viewbox = f"{west},{north},{east},{south}"
            self._viewboxes[key] = viewbox
        return self._viewboxes[key]

    async def geocode(self, address: str, expected_locality: str | None = None) -> GeocodeResult:
        candidates = await self._search({"q": address, "limit": "5"})
        if candidates is None:
            return GeocodeResult(source=GeocodeSource.failed, candidate_count=0)

        # Free-text search reads "…, valencia" as the province and may only return
        # same-name streets in other towns. Retry bounded to the town's area.
        if expected_locality and not any(
            in_locality(_locality(c), expected_locality) for c in candidates
        ):
            viewbox = await self._locality_viewbox(expected_locality)
            if viewbox:
                bounded = await self._search(
                    {"q": _street_part(address), "viewbox": viewbox, "bounded": "1", "limit": "10"}
                )
                if bounded and any(in_locality(_locality(c), expected_locality) for c in bounded):
                    candidates = bounded

        if not candidates:
            return GeocodeResult(source=GeocodeSource.failed, candidate_count=0)

        best, distinct = pick_candidate(candidates, _locality, _coords, expected_locality)
        lat, lon = _coords(best)
        return GeocodeResult(
            formatted_address=best.get("display_name"),
            lat=lat,
            lon=lon,
            source=GeocodeSource.nominatim,
            importance=float(best.get("importance", 0.0)),
            address_type=_parse_address_type(best),
            bbox_area_deg2=_bbox_area(best.get("boundingbox")),
            house_number_resolved=_has_house_number(best),
            locality=_locality(best),
            candidate_count=distinct,
            raw_response=best,
        )
