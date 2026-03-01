from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from routetools.address_cleaner.models import RawAddress


class AddressIngester(ABC):
    @abstractmethod
    def ingest(self) -> AsyncIterator[RawAddress]:
        """Yield RawAddress objects from the source."""
        ...
