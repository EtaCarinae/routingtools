from __future__ import annotations

import httpx

from routetools.address_cleaner.errors import ProviderAuthError
from routetools.address_cleaner.geocoding.base import Geocoder, pick_candidate
from routetools.address_cleaner.models import AddressType, GeocodeResult, GeocodeSource

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

_GOOGLE_TYPE_MAP: dict[str, AddressType] = {
    "street_address": AddressType.house,
    "premise": AddressType.house,
    "route": AddressType.road,
    "sublocality": AddressType.suburb,
    "neighborhood": AddressType.suburb,
    "locality": AddressType.city,
    "administrative_area_level_1": AddressType.city,
    "administrative_area_level_2": AddressType.city,
}


def _parse_address_type(types: list[str]) -> AddressType:
    for t in types:
        if t in _GOOGLE_TYPE_MAP:
            return _GOOGLE_TYPE_MAP[t]
    return AddressType.unknown


def _has_house_number(components: list[dict]) -> bool:
    return any("street_number" in c.get("types", []) for c in components)


def _locality(components: list[dict]) -> str | None:
    for c in components:
        if "locality" in c.get("types", []):
            return c.get("long_name")
    return None


def _coords(result: dict) -> tuple[float | None, float | None]:
    location = result.get("geometry", {}).get("location", {})
    return location.get("lat"), location.get("lng")


def _viewport_area(viewport: dict) -> float | None:
    try:
        ne = viewport["northeast"]
        sw = viewport["southwest"]
        lat_diff = abs(ne["lat"] - sw["lat"])
        lng_diff = abs(ne["lng"] - sw["lng"])
        return lat_diff * lng_diff
    except (KeyError, TypeError):
        return None


class GoogleGeocoder(Geocoder):
    def __init__(self, api_key: str, region: str = "es") -> None:
        self._api_key = api_key
        self._region = region

    async def geocode(self, address: str, expected_locality: str | None = None) -> GeocodeResult:
        params = {
            "address": address,
            "key": self._api_key,
            "region": self._region,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(_GEOCODE_URL, params=params)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise ProviderAuthError(
                    f"Google rejected the API key (HTTP {exc.response.status_code})."
                ) from None
            return GeocodeResult(source=GeocodeSource.failed, candidate_count=0)
        except Exception:
            return GeocodeResult(source=GeocodeSource.failed, candidate_count=0)

        status = data.get("status", "")
        if status == "REQUEST_DENIED":
            raise ProviderAuthError(f"Google Maps API denied: {data.get('error_message', '')}")
        if status not in ("OK", "ZERO_RESULTS"):
            return GeocodeResult(source=GeocodeSource.failed, candidate_count=0)

        results: list[dict] = data.get("results", [])
        if not results:
            return GeocodeResult(source=GeocodeSource.failed, candidate_count=0)

        best, distinct = pick_candidate(
            results,
            lambda r: _locality(r.get("address_components", [])),
            _coords,
            expected_locality,
        )
        location = best.get("geometry", {}).get("location", {})
        viewport = best.get("geometry", {}).get("viewport")
        components = best.get("address_components", [])
        types = best.get("types", [])

        return GeocodeResult(
            formatted_address=best.get("formatted_address"),
            lat=location.get("lat"),
            lon=location.get("lng"),
            source=GeocodeSource.google,
            importance=None,
            address_type=_parse_address_type(types),
            bbox_area_deg2=_viewport_area(viewport) if viewport else None,
            house_number_resolved=_has_house_number(components),
            locality=_locality(components),
            candidate_count=distinct,
            raw_response=best,
        )
