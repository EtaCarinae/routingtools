from __future__ import annotations

import csv
from collections.abc import AsyncIterator
from pathlib import Path

from routetools.address_cleaner.ingestion.base import AddressIngester
from routetools.address_cleaner.models import RawAddress


class CsvIngester(AddressIngester):
    def __init__(self, path: str | Path, address_column: str = "address") -> None:
        self.path = Path(path)
        self.address_column = address_column

    async def ingest(self) -> AsyncIterator[RawAddress]:
        with self.path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row_index, row in enumerate(reader):
                value = row.get(self.address_column, "").strip()
                if not value:
                    continue
                yield RawAddress(
                    original=value,
                    source_row=row_index,
                    source_file=str(self.path),
                )
