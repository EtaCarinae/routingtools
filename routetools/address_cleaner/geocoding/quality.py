from __future__ import annotations

from routetools.address_cleaner.models import AddressType, GeocodeResult

_TYPE_SCORES: dict[AddressType, float] = {
    AddressType.house: 1.0,
    AddressType.road: 0.6,
    AddressType.suburb: 0.3,
    AddressType.city: 0.1,
    AddressType.unknown: 0.0,
}

_WEIGHTS = {
    "importance": 0.35,
    "type": 0.30,
    "bbox": 0.20,
    "house_number": 0.10,
}

_AMBIGUITY_PENALTY = -0.15


def score(result: GeocodeResult, bbox_ceiling_deg2: float = 0.01) -> float:
    """Compute a quality score in [0.0, 1.0] for a geocode result."""
    importance_score = result.importance or 0.0
    type_score = _TYPE_SCORES.get(result.address_type, 0.0)

    if result.bbox_area_deg2 is not None:
        bbox_score = 1.0 - min(result.bbox_area_deg2 / bbox_ceiling_deg2, 1.0)
    else:
        bbox_score = 0.0

    house_number_score = 1.0 if result.house_number_resolved else 0.0

    raw = (
        importance_score * _WEIGHTS["importance"]
        + type_score * _WEIGHTS["type"]
        + bbox_score * _WEIGHTS["bbox"]
        + house_number_score * _WEIGHTS["house_number"]
    )

    if result.candidate_count > 1:
        raw += _AMBIGUITY_PENALTY

    return max(0.0, min(1.0, raw))
