from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class AddressType(StrEnum):
    house = "house"
    road = "road"
    suburb = "suburb"
    city = "city"
    unknown = "unknown"


class GeocodeSource(StrEnum):
    nominatim = "nominatim"
    google = "google"
    opencage = "opencage"
    failed = "failed"


class RawAddress(BaseModel):
    original: str
    source_row: int | None = None
    source_file: str | None = None


class GeocodeResult(BaseModel):
    formatted_address: str | None = None
    lat: float | None = None
    lon: float | None = None
    source: GeocodeSource
    importance: float | None = None
    address_type: AddressType = AddressType.unknown
    bbox_area_deg2: float | None = None
    house_number_resolved: bool = False
    locality: str | None = None
    # Distinct locations among the candidates (~100 m apart), not raw result count:
    # several POIs in the same building are not ambiguity.
    candidate_count: int = 0
    raw_response: dict | None = Field(default=None, exclude=True)
    quality_score: float | None = None


class NormalizedAddress(BaseModel):
    original: str
    pre_normalized: str
    geocode: GeocodeResult
    confidence: float
    flagged: bool = False
    flag_reason: str | None = None
    dedupe_cluster_id: str | None = None
    escalated: bool = False
    arbiter_consulted: bool = False
    arbiter_rationale: str | None = None


class CleaningReport(BaseModel):
    run_id: str
    summary: dict
    results: list[NormalizedAddress]
