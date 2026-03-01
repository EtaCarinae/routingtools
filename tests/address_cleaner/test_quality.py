import pytest

from routetools.address_cleaner.geocoding.quality import score
from routetools.address_cleaner.models import AddressType, GeocodeResult, GeocodeSource


def _make_result(**kwargs) -> GeocodeResult:
    defaults = dict(
        source=GeocodeSource.nominatim,
        importance=0.5,
        address_type=AddressType.house,
        bbox_area_deg2=0.001,
        house_number_resolved=True,
        candidate_count=1,
    )
    defaults.update(kwargs)
    return GeocodeResult(**defaults)


def test_perfect_result():
    result = _make_result(
        importance=1.0,
        address_type=AddressType.house,
        bbox_area_deg2=0.0,
        house_number_resolved=True,
        candidate_count=1,
    )
    s = score(result, bbox_ceiling_deg2=0.01)
    # Max possible: 0.35 + 0.30 + 0.20 + 0.10 = 0.95
    assert s == pytest.approx(0.95, abs=0.001)


def test_ambiguity_penalty():
    r1 = _make_result(candidate_count=1)
    r2 = _make_result(candidate_count=3)
    assert score(r1) > score(r2)
    assert score(r1) - score(r2) == pytest.approx(0.15, abs=0.001)


def test_unknown_type_scores_zero():
    result = _make_result(address_type=AddressType.unknown)
    s = score(result)
    # type contributes 0, other components may still give > 0
    result_house = _make_result(address_type=AddressType.house)
    assert score(result_house) > s


def test_score_clamps_to_zero_on_penalty():
    # Very bad result with ambiguity penalty should not go below 0
    result = _make_result(
        importance=0.0,
        address_type=AddressType.unknown,
        bbox_area_deg2=0.01,
        house_number_resolved=False,
        candidate_count=5,
    )
    s = score(result, bbox_ceiling_deg2=0.01)
    assert s >= 0.0


def test_score_without_bbox():
    result = _make_result(bbox_area_deg2=None)
    s = score(result)
    # Should not raise, bbox_score treated as 0
    assert 0.0 <= s <= 1.0


def test_road_type_lower_than_house():
    house = _make_result(address_type=AddressType.house)
    road = _make_result(address_type=AddressType.road)
    assert score(house) > score(road)
