from __future__ import annotations

from abc import ABC, abstractmethod

from routetools.address_cleaner.models import GeocodeResult


class Geocoder(ABC):
    @abstractmethod
    async def geocode(self, address: str) -> GeocodeResult:
        """Geocode an address string.

        Never raises on soft failures — returns source=failed instead.
        Only raises on hard errors (auth failure, timeout after retries).
        """
        ...
