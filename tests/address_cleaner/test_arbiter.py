import json

import pytest

from routetools.address_cleaner.arbiter.llm_arbiter import (
    MAX_CONFIDENCE_ADJUSTMENT,
    build_prompt,
    parse_decision,
)
from routetools.address_cleaner.models import AddressType, GeocodeResult, GeocodeSource


def _args(**kw) -> str:
    base = {"decision": "accept", "rationale": "ok", "confidence_adjustment": 0.05}
    base.update(kw)
    return json.dumps(base)


def test_parse_valid_decision():
    d = parse_decision(_args())
    assert (d.decision, d.rationale, d.confidence_adjustment) == ("accept", "ok", 0.05)


@pytest.mark.parametrize("adjustment, expected", [(0.9, 0.2), (-0.9, -0.2), (0.1, 0.1)])
def test_adjustment_is_clamped(adjustment, expected):
    d = parse_decision(_args(confidence_adjustment=adjustment))
    assert d.confidence_adjustment == pytest.approx(expected)
    assert abs(d.confidence_adjustment) <= MAX_CONFIDENCE_ADJUSTMENT


@pytest.mark.parametrize(
    "arguments",
    [
        "not json",
        json.dumps({"rationale": "missing decision"}),
        _args(decision="escalate_to_mars"),
        _args(confidence_adjustment="a lot"),
    ],
)
def test_malformed_output_becomes_flag(arguments):
    d = parse_decision(arguments)
    assert d.decision == "flag_for_review"
    assert d.confidence_adjustment == 0.0


def test_prompt_contains_comparison_fields():
    result = GeocodeResult(
        source=GeocodeSource.nominatim,
        formatted_address="5, Avenida de la Constitución, Gines, Sevilla, España",
        address_type=AddressType.house,
        house_number_resolved=True,
        locality="Gines",
        candidate_count=1,
    )
    prompt = build_prompt(
        "Avda. de la Constitución 5, Sevilla",
        "avenida de la constitución 5, sevilla",
        result,
        0.75,
        "sevilla",
    )
    assert "Locality named in the input: sevilla" in prompt
    assert "Result locality: Gines" in prompt
    assert "house number resolved: yes" in prompt
    assert "Quality score: 0.75" in prompt
