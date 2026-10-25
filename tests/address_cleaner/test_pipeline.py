from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from routetools.address_cleaner.config import AddressCleanerSettings
from routetools.address_cleaner.errors import ProviderAuthError
from routetools.address_cleaner.ingestion.base import AddressIngester
from routetools.address_cleaner.models import GeocodeSource, RawAddress
from routetools.address_cleaner.pipeline import AddressCleanerPipeline

_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_OPENCAGE = "https://api.opencagedata.com/geocode/v1/json"
_OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"


class _ListIngester(AddressIngester):
    def __init__(self, addresses: list[str]) -> None:
        self._addresses = addresses

    async def ingest(self) -> AsyncIterator[RawAddress]:
        for i, addr in enumerate(self._addresses):
            yield RawAddress(original=addr, source_row=i)


def _nominatim_hit(display_name: str, locality: str, house_number: str | None = "10", **kw):
    hit = {
        "display_name": display_name,
        "lat": "40.41650",
        "lon": "-3.70379",
        "importance": 0.0001,
        "category": "place",
        "type": "house",
        "place_rank": 30,
        "address": {"city": locality, **({"house_number": house_number} if house_number else {})},
        "boundingbox": ["40.4164", "40.4166", "-3.7039", "-3.7036"],
    }
    hit.update(kw)
    return hit


_MADRID_HIT = [_nominatim_hit("10, Calle Mayor, Madrid, España", "Madrid")]

# Real-world case: the street exists, but in Gines, not in the city of Sevilla.
_GINES_HIT = [
    _nominatim_hit(
        "5, Avenida de la Constitución, Gines, Sevilla, España",
        "Gines",
        house_number="5",
        lat="37.3858",
        lon="-6.0741",
        boundingbox=["37.3858", "37.3859", "-6.0742", "-6.0741"],
    )
]

_COUNTRY_LEVEL_HIT = [
    _nominatim_hit(
        "España",
        "",
        house_number=None,
        type="country",
        category="boundary",
        place_rank=4,
        boundingbox=["36.0", "44.0", "-9.0", "3.0"],
    )
]

_OPENCAGE_SEVILLA = {
    "results": [
        {
            "formatted": "Avenida de la Constitución, 5, 41004 Sevilla, España",
            "geometry": {"lat": 37.3878, "lng": -5.9945},
            "confidence": 10,
            "components": {
                "house_number": "5",
                "road": "Avenida de la Constitución",
                "city": "Sevilla",
            },
            "bounds": {
                "northeast": {"lat": 37.38785, "lng": -5.99445},
                "southwest": {"lat": 37.38775, "lng": -5.99455},
            },
        }
    ]
}


def _llm_decision(decision: str, rationale: str = "motivo", adjustment: float = 0.0):
    args = {"decision": decision, "rationale": rationale, "confidence_adjustment": adjustment}
    return httpx.Response(
        200,
        json={
            "id": "gen-1",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "address_decision",
                                    "arguments": json.dumps(args),
                                },
                            }
                        ],
                    },
                }
            ],
        },
    )


def _make_settings(**overrides) -> AddressCleanerSettings:
    defaults = dict(
        nominatim_user_agent="test-agent/1.0",
        google_maps_api_key="",
        opencage_api_key="",
        openrouter_api_key="",
        escalation_provider="opencage",
        arbiter_model="test-model",
        country_codes="es",
        quality_high_threshold=0.80,
        quality_low_threshold=0.50,
        bbox_ceiling_deg2=0.01,
        dedupe_decimal_places=5,
        nominatim_rps=100.0,  # fast for tests
        abbreviations={"c/": "calle", "avda.": "avenida"},
    )
    defaults.update(overrides)
    return AddressCleanerSettings.model_construct(**defaults)


async def _run(addresses: list[str], **settings):
    pipeline = AddressCleanerPipeline(_ListIngester(addresses), settings=_make_settings(**settings))
    return await pipeline.run()


@respx.mock
async def test_high_quality_accepts_nominatim():
    respx.get(_NOMINATIM).mock(return_value=httpx.Response(200, json=_MADRID_HIT))

    report = await _run(["C/ Mayor 10, Madrid"])

    result = report.results[0]
    assert result.geocode.source == GeocodeSource.nominatim
    assert result.geocode.locality == "Madrid"
    assert result.confidence == pytest.approx(1.0)
    assert not result.escalated
    assert not result.arbiter_consulted
    assert not result.flagged


@respx.mock
async def test_low_quality_without_escalation_geocoder_is_flagged():
    respx.get(_NOMINATIM).mock(return_value=httpx.Response(200, json=_COUNTRY_LEVEL_HIT))

    report = await _run(["Calle Inventada 999, Ciudad Desconocida"])

    result = report.results[0]
    assert not result.escalated  # nothing to escalate to
    assert result.flagged
    assert result.flag_reason == "Low quality, no escalation geocoder configured"


@respx.mock
async def test_low_quality_escalates_to_opencage():
    respx.get(_NOMINATIM).mock(return_value=httpx.Response(200, json=[]))
    respx.get(_OPENCAGE).mock(return_value=httpx.Response(200, json=_OPENCAGE_SEVILLA))

    report = await _run(["Avda. de la Constitución 5, Sevilla"], opencage_api_key="k")

    result = report.results[0]
    assert result.escalated
    assert result.geocode.source == GeocodeSource.opencage
    assert result.geocode.locality == "Sevilla"
    assert not result.flagged
    assert result.confidence >= 0.8


@respx.mock
async def test_grey_zone_without_arbiter_is_flagged():
    respx.get(_NOMINATIM).mock(return_value=httpx.Response(200, json=_GINES_HIT))

    report = await _run(["Avda. de la Constitución 5, Sevilla"])

    result = report.results[0]
    assert result.flagged
    assert not result.arbiter_consulted  # no arbiter was configured, so none was consulted
    assert result.flag_reason == "Grey zone, no arbiter configured"


@respx.mock
async def test_grey_zone_arbiter_escalates_wrong_locality():
    respx.get(_NOMINATIM).mock(return_value=httpx.Response(200, json=_GINES_HIT))
    respx.get(_OPENCAGE).mock(return_value=httpx.Response(200, json=_OPENCAGE_SEVILLA))
    llm = respx.post(_OPENROUTER).mock(
        return_value=_llm_decision("escalate", "El resultado está en Gines, no en Sevilla.")
    )

    report = await _run(
        ["Avda. de la Constitución 5, Sevilla"], opencage_api_key="k", openrouter_api_key="k"
    )

    result = report.results[0]
    assert result.arbiter_consulted
    assert result.escalated
    assert result.geocode.locality == "Sevilla"
    assert result.arbiter_rationale == "El resultado está en Gines, no en Sevilla."
    assert not result.flagged
    # The prompt gives the LLM both localities to compare
    prompt = json.loads(llm.calls.last.request.content)["messages"][1]["content"]
    assert "Locality named in the input: sevilla" in prompt
    assert "Result locality: Gines" in prompt


@respx.mock
async def test_grey_zone_arbiter_escalation_without_geocoder_is_flagged():
    respx.get(_NOMINATIM).mock(return_value=httpx.Response(200, json=_GINES_HIT))
    respx.post(_OPENROUTER).mock(return_value=_llm_decision("escalate"))

    report = await _run(["Avda. de la Constitución 5, Sevilla"], openrouter_api_key="k")

    result = report.results[0]
    assert result.flagged
    assert not result.escalated
    assert result.flag_reason == "Arbiter asked to escalate, but escalation found nothing"


@respx.mock
async def test_grey_zone_arbiter_accept_adjusts_confidence_within_bounds():
    respx.get(_NOMINATIM).mock(return_value=httpx.Response(200, json=_GINES_HIT))
    respx.post(_OPENROUTER).mock(return_value=_llm_decision("accept", adjustment=0.9))

    report = await _run(["Avda. de la Constitución 5, Sevilla"], openrouter_api_key="k")

    result = report.results[0]
    assert not result.flagged
    assert result.geocode.source == GeocodeSource.nominatim
    assert result.confidence == pytest.approx(0.75 + 0.2)  # adjustment capped at 0.2


@respx.mock
async def test_arbiter_bad_key_stops_the_run():
    respx.get(_NOMINATIM).mock(return_value=httpx.Response(200, json=_GINES_HIT))
    respx.post(_OPENROUTER).mock(
        return_value=httpx.Response(401, json={"error": {"message": "User not found."}})
    )

    with pytest.raises(ProviderAuthError, match="OPENROUTER_API_KEY"):
        await _run(["Avda. de la Constitución 5, Sevilla"], openrouter_api_key="bad")


@respx.mock
async def test_escalation_bad_key_stops_the_run():
    respx.get(_NOMINATIM).mock(return_value=httpx.Response(200, json=[]))
    respx.get(_OPENCAGE).mock(return_value=httpx.Response(401, json={}))

    with pytest.raises(ProviderAuthError, match="OPENCAGE_API_KEY"):
        await _run(["Calle Mayor 10, Madrid"], opencage_api_key="bad")


@respx.mock
async def test_nominatim_timeout_does_not_abort_batch():
    respx.get(_NOMINATIM).mock(side_effect=httpx.ReadTimeout("slow"))

    report = await _run(["Calle Mayor 10, Madrid", "Gran Vía 28, Madrid"])

    assert len(report.results) == 2
    assert all(r.flagged for r in report.results)


@respx.mock
async def test_deduplication():
    """Two spellings of the same address geocode to the same point → same cluster."""
    respx.get(_NOMINATIM).mock(return_value=httpx.Response(200, json=_MADRID_HIT))

    report = await _run(["C/ Mayor 10, Madrid", "Calle Mayor 10, Madrid"])

    assert len({r.dedupe_cluster_id for r in report.results}) == 1


@respx.mock
async def test_summary_counts():
    respx.get(_NOMINATIM).mock(
        side_effect=[
            httpx.Response(200, json=_MADRID_HIT),
            httpx.Response(200, json=_GINES_HIT),
        ]
    )

    report = await _run(["C/ Mayor 10, Madrid", "Avda. de la Constitución 5, Sevilla"])

    assert report.summary["total"] == 2
    assert report.summary["flagged"] == 1
    assert report.summary["geocode_sources"] == {"nominatim": 2}
    assert report.summary["unique_clusters"] == 2


async def test_csv_ingester_integration(sample_csv_path):
    from routetools.address_cleaner.ingestion.csv_ingester import CsvIngester

    rows = [raw async for raw in CsvIngester(sample_csv_path).ingest()]

    assert len(rows) == 8
    assert rows[0].original == "C/ Mayor 10, Madrid"
    assert rows[0].source_row == 0


@respx.mock
async def test_escalated_result_in_other_town_is_flagged():
    """Real case: OpenCage found "Calle A 12" in Miranda de Ebro for an input saying Burgos."""
    respx.get(_NOMINATIM).mock(return_value=httpx.Response(200, json=[]))
    miranda = {
        "results": [
            {
                **_OPENCAGE_SEVILLA["results"][0],
                "formatted": "Calle A, 12, 09200 Miranda de Ebro, España",
                "components": {"house_number": "12", "city": "Miranda de Ebro"},
            }
        ]
    }
    respx.get(_OPENCAGE).mock(return_value=httpx.Response(200, json=miranda))

    report = await _run(["Pol. Industrial Norte, Calle A 12, Burgos"], opencage_api_key="k")

    result = report.results[0]
    assert result.escalated
    assert result.confidence == pytest.approx(0.75)
    assert result.flagged
    assert result.flag_reason == "Low quality, still uncertain after escalation"


@respx.mock
async def test_escalation_never_replaces_a_better_result():
    respx.get(_NOMINATIM).mock(return_value=httpx.Response(200, json=_GINES_HIT))
    worse = {
        "results": [
            {
                "formatted": "Sevilla, España",
                "geometry": {"lat": 37.38, "lng": -5.98},
                "confidence": 1,
                "components": {"city": "Sevilla"},
            }
        ]
    }
    respx.get(_OPENCAGE).mock(return_value=httpx.Response(200, json=worse))
    respx.post(_OPENROUTER).mock(return_value=_llm_decision("escalate"))

    report = await _run(
        ["Avda. de la Constitución 5, Sevilla"], opencage_api_key="k", openrouter_api_key="k"
    )

    result = report.results[0]
    assert result.geocode.source == GeocodeSource.nominatim  # kept the better one
    assert result.flagged
    assert result.flag_reason == "Arbiter asked to escalate, still uncertain after it"


@respx.mock
async def test_nominatim_retries_bounded_to_the_named_town():
    """ "Valencia" is read as the province: free text only finds Calle Colón in other towns."""
    requena = _nominatim_hit("1, Calle Colón, Requena, València", "Requena", house_number="1")
    city = {"addresstype": "city", "boundingbox": ["39.27", "39.56", "-0.43", "-0.27"]}
    valencia = _nominatim_hit("1, Carrer de Colón, València", "València", house_number="1")

    def search(request):
        params = request.url.params
        if "city" in params:
            return httpx.Response(200, json=[city])
        if params.get("bounded") == "1":
            assert params["q"] == "calle colón 1"
            assert params["viewbox"] == "-0.43,39.56,-0.27,39.27"
            return httpx.Response(200, json=[valencia])
        return httpx.Response(200, json=[requena])

    respx.get(_NOMINATIM).mock(side_effect=search)

    report = await _run(["C/ Colón 1, Valencia"])

    result = report.results[0]
    assert result.geocode.locality == "València"
    assert result.confidence == pytest.approx(1.0)
    assert not result.flagged


@pytest.mark.parametrize(
    "hit, expected",
    [
        ({"category": "highway", "type": "pedestrian", "place_rank": 25}, "road"),
        ({"category": "shop", "type": "bakery", "place_rank": 30}, "house"),
        ({"category": "boundary", "type": "administrative", "place_rank": 16}, "city"),
    ],
)
def test_nominatim_address_type(hit, expected):
    from routetools.address_cleaner.geocoding.nominatim import _parse_address_type

    assert _parse_address_type({"address": {}, **hit}) == expected
