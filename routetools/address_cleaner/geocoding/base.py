from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from typing import TypeVar

from routetools.address_cleaner.models import GeocodeResult
from routetools.address_cleaner.normalizer import fold

T = TypeVar("T")


class Geocoder(ABC):
    @abstractmethod
    async def geocode(self, address: str, expected_locality: str | None = None) -> GeocodeResult:
        """Geocode an address string, preferring results in `expected_locality`.

        Never raises on soft failures (no results, timeouts, 5xx) — returns
        source=failed instead, so one bad row doesn't abort a whole batch.
        Raises ProviderAuthError when the credentials are rejected.
        """
        ...


def count_distinct_locations(
    points: Iterable[tuple[float | None, float | None]],
    decimal_places: int = 3,
) -> int:
    """Count candidates at distinct locations (3 decimals ≈ 100 m)."""
    return len(
        {
            (round(lat, decimal_places), round(lon, decimal_places))
            for lat, lon in points
            if lat is not None and lon is not None
        }
    )


def in_locality(locality: str | None, expected_locality: str | None) -> bool:
    return bool(locality and expected_locality and fold(locality) == fold(expected_locality))


def pick_candidate(
    candidates: list[T],
    locality_of: Callable[[T], str | None],
    coords_of: Callable[[T], tuple[float | None, float | None]],
    expected_locality: str | None,
) -> tuple[T, int]:
    """Best candidate and how many distinct locations compete with it.

    Keeps the provider's ranking but prefers candidates in the locality the input
    names: Nominatim reads "Valencia" as the province and ranks Calle Colón 1 in
    Requena first. Once the town is known, same-name streets in other towns are
    not ambiguity, so only the candidates in that town are counted.
    """
    pool = [c for c in candidates if in_locality(locality_of(c), expected_locality)]
    if not pool:
        pool = candidates
    return pool[0], count_distinct_locations(coords_of(c) for c in pool)
