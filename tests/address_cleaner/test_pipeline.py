from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from routetools.address_cleaner.config import AddressCleanerSettings
from routetools.address_cleaner.ingestion.base import AddressIngester
from routetools.address_cleaner.models import GeocodeSource, RawAddress
from routetools.address_cleaner.pipeline import AddressCleanerPipeline


class _ListIngester(AddressIngester):
    def __init__(self, addresses: list[str]) -> None:
        self._addresses = addresses

    async def ingest(self) -> AsyncIterator[RawAddress]:
        for i, addr in enumerate(self._addresses):
            yield RawAddress(original=addr, source_row=i)


_HIGH_QUALITY_NOMINATIM_RESPONSE = [
    {
        "display_name": "Calle Mayor, 10, Madrid, España",
        "lat": "40.41650",
        "lon": "-3.70379",
        "importance": 0.95,
        "type": "house",
        "class": "place",
        "address": {"house_number": "10", "road": "Calle Mayor"},
        "boundingbox": ["40.4164", "40.4166", "-3.7039", "-3.7036"],
    }
]


def _make_settings(**overrides) -> AddressCleanerSettings:
    defaults = dict(
        nominatim_user_agent="test-agent/1.0",
        google_maps_api_key="",
        anthropic_api_key="",
        quality_high_threshold=0.80,
        quality_low_threshold=0.50,
        nominatim_rps=100.0,  # fast for tests
    )
    defaults.update(overrides)
    return AddressCleanerSettings.model_construct(**defaults)


@pytest.mark.asyncio
async def test_pipeline_high_quality_accepts_nominatim():
    settings = _make_settings()
    ingester = _ListIngester(["C/ Mayor 10, Madrid"])

    with respx.mock(base_url="https://nominatim.openstreetmap.org") as mock:
        mock.get("/search").mock(
            return_value=httpx.Response(200, json=_HIGH_QUALITY_NOMINATIM_RESPONSE)
        )
        pipeline = AddressCleanerPipeline(ingester, settings=settings)
        report = await pipeline.run()

    assert len(report.results) == 1
    result = report.results[0]
    assert result.geocode.source == GeocodeSource.nominatim
    assert not result.escalated_to_google
    assert not result.arbiter_consulted
    assert not result.flagged
    assert result.confidence >= settings.quality_high_threshold


@pytest.mark.asyncio
async def test_pipeline_low_quality_escalates_without_google():
    """When quality is low and Google is not configured, result should be flagged."""
    settings = _make_settings()
    ingester = _ListIngester(["Calle Inventada 999, Ciudad Desconocida"])

    low_quality_response = [
        {
            "display_name": "Spain",
            "lat": "40.0",
            "lon": "-3.0",
            "importance": 0.1,
            "type": "city",
            "class": "place",
            "address": {},
            "boundingbox": ["36.0", "44.0", "-9.0", "3.0"],
        }
    ]

    with respx.mock(base_url="https://nominatim.openstreetmap.org") as mock:
        mock.get("/search").mock(
            return_value=httpx.Response(200, json=low_quality_response)
        )
        pipeline = AddressCleanerPipeline(ingester, settings=settings)
        report = await pipeline.run()

    result = report.results[0]
    assert result.escalated_to_google
    assert result.flagged  # Google not configured, so should be flagged


@pytest.mark.asyncio
async def test_pipeline_deduplication():
    """Two addresses that geocode to the same lat/lon get the same cluster."""
    settings = _make_settings()
    ingester = _ListIngester(["C/ Mayor 10, Madrid", "Calle Mayor 10, Madrid"])

    with respx.mock(base_url="https://nominatim.openstreetmap.org") as mock:
        mock.get("/search").mock(
            return_value=httpx.Response(200, json=_HIGH_QUALITY_NOMINATIM_RESPONSE)
        )
        pipeline = AddressCleanerPipeline(ingester, settings=settings)
        report = await pipeline.run()

    assert len(report.results) == 2
    cluster_ids = {r.dedupe_cluster_id for r in report.results}
    assert len(cluster_ids) == 1  # same location → same cluster


@pytest.mark.asyncio
async def test_pipeline_nominatim_failure_flags():
    """When Nominatim returns empty, result should eventually be flagged."""
    settings = _make_settings()
    ingester = _ListIngester(["Dirección completamente inválida xyzzy"])

    with respx.mock(base_url="https://nominatim.openstreetmap.org") as mock:
        mock.get("/search").mock(return_value=httpx.Response(200, json=[]))
        pipeline = AddressCleanerPipeline(ingester, settings=settings)
        report = await pipeline.run()

    result = report.results[0]
    assert result.geocode.source == GeocodeSource.failed or result.flagged


@pytest.mark.asyncio
async def test_pipeline_summary_counts():
    settings = _make_settings()
    ingester = _ListIngester(["C/ Mayor 10, Madrid", "Avda. Libertad 5, Sevilla"])

    with respx.mock(base_url="https://nominatim.openstreetmap.org") as mock:
        mock.get("/search").mock(
            return_value=httpx.Response(200, json=_HIGH_QUALITY_NOMINATIM_RESPONSE)
        )
        pipeline = AddressCleanerPipeline(ingester, settings=settings)
        report = await pipeline.run()

    assert report.summary["total"] == 2
    assert "geocode_sources" in report.summary
    assert "average_confidence" in report.summary


@pytest.mark.asyncio
async def test_csv_ingester_integration(sample_csv_path):
    from routetools.address_cleaner.ingestion.csv_ingester import CsvIngester

    ingester = CsvIngester(sample_csv_path)
    rows = []
    async for raw in ingester.ingest():
        rows.append(raw)

    assert len(rows) == 8
    assert rows[0].original == "C/ Mayor 10, Madrid"
    assert rows[0].source_row == 0
