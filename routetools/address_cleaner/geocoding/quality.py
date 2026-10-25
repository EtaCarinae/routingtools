from __future__ import annotations

from routetools.address_cleaner.models import AddressType, GeocodeResult
from routetools.address_cleaner.normalizer import fold

_TYPE_SCORES: dict[AddressType, float] = {
    AddressType.house: 1.0,
    AddressType.road: 0.6,
    AddressType.suburb: 0.3,
    AddressType.city: 0.1,
    AddressType.unknown: 0.0,
}

# Nominatim's `importance` is deliberately not used: it measures how notable a
# place is (Wikipedia links), not how precise the match is — a correct
# house-level hit scores ~0.0001.
_WEIGHTS = {
    "type": 0.40,
    "locality": 0.25,
    "bbox": 0.20,
    "house_number": 0.15,
}

_AMBIGUITY_PENALTY = -0.15

# Used when the input names no locality or the result has none: neither
# confirms nor contradicts the match.
_LOCALITY_UNKNOWN = 0.5


def locality_match(result: GeocodeResult, expected_locality: str | None) -> float | None:
    """1.0 if the result is in the locality the input asked for, 0.0 if it is
    somewhere else, None if there is nothing to compare."""
    if not expected_locality or not result.locality:
        return None
    return 1.0 if fold(expected_locality) == fold(result.locality) else 0.0


def score(
    result: GeocodeResult,
    bbox_ceiling_deg2: float = 0.01,
    expected_locality: str | None = None,
) -> float:
    """Compute a quality score in [0.0, 1.0] for a geocode result."""
    type_score = _TYPE_SCORES.get(result.address_type, 0.0)

    match = locality_match(result, expected_locality)
    locality_score = _LOCALITY_UNKNOWN if match is None else match

    if result.bbox_area_deg2 is not None:
        bbox_score = 1.0 - min(result.bbox_area_deg2 / bbox_ceiling_deg2, 1.0)
    else:
        bbox_score = 0.0

    house_number_score = 1.0 if result.house_number_resolved else 0.0

    raw = (
        type_score * _WEIGHTS["type"]
        + locality_score * _WEIGHTS["locality"]
        + bbox_score * _WEIGHTS["bbox"]
        + house_number_score * _WEIGHTS["house_number"]
    )

    if result.candidate_count > 1:
        raw += _AMBIGUITY_PENALTY

    return max(0.0, min(1.0, raw))
