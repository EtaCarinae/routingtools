import pytest

from routetools.address_cleaner.geocoding.base import count_distinct_locations
from routetools.address_cleaner.geocoding.quality import locality_match, score
from routetools.address_cleaner.models import AddressType, GeocodeResult, GeocodeSource


def _make_result(**kwargs) -> GeocodeResult:
    defaults = dict(
        source=GeocodeSource.nominatim,
        importance=0.0001,
        address_type=AddressType.house,
        bbox_area_deg2=0.0,
        house_number_resolved=True,
        locality="Madrid",
        candidate_count=1,
    )
    defaults.update(kwargs)
    return GeocodeResult(**defaults)


def test_perfect_result_scores_one():
    # Real house-level Nominatim hits have importance ~0.0001; it must not matter.
    assert score(_make_result(), expected_locality="madrid") == pytest.approx(1.0)


def test_importance_is_ignored():
    low = _make_result(importance=0.0001)
    high = _make_result(importance=0.9)
    assert score(low, expected_locality="madrid") == score(high, expected_locality="madrid")


def test_wrong_locality_lands_in_grey_zone():
    # "Avda. de la Constitución 5, Sevilla" resolved to the same street in Gines
    result = _make_result(locality="Gines")
    s = score(result, expected_locality="sevilla")
    assert s == pytest.approx(0.75)
    assert 0.5 <= s < 0.8


def test_locality_unknown_is_neutral():
    s = score(_make_result(), expected_locality=None)
    assert s == pytest.approx(1.0 - 0.25 * 0.5)


@pytest.mark.parametrize(
    "expected, actual, match",
    [
        ("valencia", "València", 1.0),
        ("a coruña", "A Coruña", 1.0),
        ("sevilla", "Gines", 0.0),
        (None, "Madrid", None),
        ("madrid", None, None),
    ],
)
def test_locality_match(expected, actual, match):
    assert locality_match(_make_result(locality=actual), expected) == match


def test_ambiguity_penalty():
    r1 = _make_result(candidate_count=1)
    r2 = _make_result(candidate_count=3)
    assert score(r1) - score(r2) == pytest.approx(0.15, abs=0.001)


def test_same_building_candidates_are_one_location():
    # Three POIs in the same building (real Nominatim output for "calle mayor 10, madrid")
    points = [(40.4164965, -3.7059265), (40.4164948, -3.7060201), (40.4164726, -3.7060265)]
    assert count_distinct_locations(points) == 1


def test_distant_candidates_are_distinct():
    assert count_distinct_locations([(40.4165, -3.7059), (41.6488, -0.8891)]) == 2


def test_unknown_type_scores_lower():
    assert score(_make_result()) > score(_make_result(address_type=AddressType.unknown))


def test_score_clamps_to_zero_on_penalty():
    result = _make_result(
        address_type=AddressType.unknown,
        bbox_area_deg2=0.01,
        house_number_resolved=False,
        locality="Gines",
        candidate_count=5,
    )
    assert score(result, bbox_ceiling_deg2=0.01, expected_locality="sevilla") == 0.0


def test_score_without_bbox():
    s = score(_make_result(bbox_area_deg2=None))
    assert 0.0 <= s <= 1.0


def test_road_type_lower_than_house():
    house = _make_result(address_type=AddressType.house)
    road = _make_result(address_type=AddressType.road, house_number_resolved=False)
    assert score(house) > score(road)


def _pick(candidates, expected):
    from routetools.address_cleaner.geocoding.base import pick_candidate

    return pick_candidate(candidates, lambda c: c[0], lambda c: c[1], expected)


def test_pick_prefers_expected_locality_over_provider_rank():
    # Real Nominatim order for "calle colón 1, valencia": Requena first
    candidates = [
        ("Requena", (39.48, -1.10)),
        ("Utiel", (39.56, -1.20)),
        ("València", (39.4699, -0.3763)),
    ]
    best, distinct = _pick(candidates, "valencia")
    assert best[0] == "València"
    assert distinct == 1  # streets in other towns are not ambiguity


def test_pick_falls_back_to_provider_rank():
    candidates = [("Requena", (39.48, -1.10)), ("Utiel", (39.56, -1.20))]
    best, distinct = _pick(candidates, "valencia")
    assert best[0] == "Requena"
    assert distinct == 2
