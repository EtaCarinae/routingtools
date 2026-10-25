from __future__ import annotations

import uuid
from collections.abc import Callable

from routetools.address_cleaner import deduplicator, normalizer
from routetools.address_cleaner.arbiter.llm_arbiter import LLMArbiter
from routetools.address_cleaner.config import AddressCleanerSettings, get_settings
from routetools.address_cleaner.geocoding import quality
from routetools.address_cleaner.geocoding.base import Geocoder
from routetools.address_cleaner.geocoding.google import GoogleGeocoder
from routetools.address_cleaner.geocoding.nominatim import NominatimGeocoder
from routetools.address_cleaner.geocoding.opencage import OpenCageGeocoder
from routetools.address_cleaner.ingestion.base import AddressIngester
from routetools.address_cleaner.models import (
    CleaningReport,
    GeocodeResult,
    GeocodeSource,
    NormalizedAddress,
)
from routetools.address_cleaner.reporter import build_summary


def _build_escalation_geocoder(cfg: AddressCleanerSettings) -> Geocoder | None:
    """The configured second geocoder, or None if it has no API key."""
    if cfg.escalation_provider == "google":
        if cfg.google_maps_api_key:
            return GoogleGeocoder(api_key=cfg.google_maps_api_key, region=cfg.country_codes)
        return None
    if cfg.escalation_provider == "opencage":
        if cfg.opencage_api_key:
            return OpenCageGeocoder(api_key=cfg.opencage_api_key, country_code=cfg.country_codes)
        return None
    raise ValueError(
        f"Unknown escalation_provider {cfg.escalation_provider!r} (use 'opencage' or 'google')"
    )


def _best(first: GeocodeResult, second: GeocodeResult) -> GeocodeResult:
    """Keep the higher-scoring result: escalating must never make things worse."""
    return second if (second.quality_score or 0.0) > (first.quality_score or 0.0) else first


class AddressCleanerPipeline:
    def __init__(
        self,
        ingester: AddressIngester,
        settings: AddressCleanerSettings | None = None,
    ) -> None:
        self._ingester = ingester
        self._cfg = settings or get_settings()

        self._nominatim = NominatimGeocoder(
            user_agent=self._cfg.nominatim_user_agent,
            country_codes=self._cfg.country_codes,
            rps=self._cfg.nominatim_rps,
        )
        self._escalation_geocoder = _build_escalation_geocoder(self._cfg)
        self._arbiter = (
            LLMArbiter(api_key=self._cfg.openrouter_api_key, model=self._cfg.arbiter_model)
            if self._cfg.openrouter_api_key
            else None
        )

    def _score(self, result: GeocodeResult, expected_locality: str | None) -> float:
        q = quality.score(result, self._cfg.bbox_ceiling_deg2, expected_locality)
        result.quality_score = q
        return q

    async def _escalate(self, address: str, expected_locality: str | None) -> GeocodeResult | None:
        """Second geocoder's scored result, or None if not configured or it found nothing."""
        if self._escalation_geocoder is None:
            return None
        result = await self._escalation_geocoder.geocode(address, expected_locality)
        if result.source == GeocodeSource.failed:
            return None
        self._score(result, expected_locality)
        return result

    async def _process_one(self, original: str) -> NormalizedAddress:
        cfg = self._cfg
        pre_normalized = normalizer.normalize(original, cfg.abbreviations)
        expected_locality = normalizer.extract_locality(pre_normalized)

        nominatim_result = await self._nominatim.geocode(pre_normalized, expected_locality)
        q_score = self._score(nominatim_result, expected_locality)

        final_geocode = nominatim_result
        confidence = q_score
        flagged = False
        flag_reason: str | None = None
        escalated = False
        arbiter_consulted = False
        arbiter_rationale: str | None = None

        if q_score >= cfg.quality_high_threshold:
            pass  # Good enough: accept Nominatim as is

        elif q_score >= cfg.quality_low_threshold:
            # Grey zone — the LLM decides what to do with it
            if self._arbiter is None:
                flagged = True
                flag_reason = "Grey zone, no arbiter configured"
            else:
                arbiter_consulted = True
                decision = await self._arbiter.arbitrate(
                    original=original,
                    pre_normalized=pre_normalized,
                    geocode_result=nominatim_result,
                    quality_score=q_score,
                    expected_locality=expected_locality,
                )
                arbiter_rationale = decision.rationale
                confidence = max(0.0, min(1.0, q_score + decision.confidence_adjustment))

                if decision.decision == "escalate":
                    escalated = self._escalation_geocoder is not None
                    escalated_result = await self._escalate(pre_normalized, expected_locality)
                    if escalated_result is None:
                        flagged = True
                        flag_reason = "Arbiter asked to escalate, but escalation found nothing"
                    else:
                        final_geocode = _best(nominatim_result, escalated_result)
                        confidence = final_geocode.quality_score or 0.0
                        if confidence < cfg.quality_high_threshold:
                            flagged = True
                            flag_reason = "Arbiter asked to escalate, still uncertain after it"
                elif decision.decision == "flag_for_review":
                    flagged = True
                    flag_reason = f"Arbiter: {decision.rationale}"

        else:
            # Low quality — go straight to the second geocoder
            escalated = self._escalation_geocoder is not None
            escalated_result = await self._escalate(pre_normalized, expected_locality)
            if escalated_result is None:
                flagged = True
                flag_reason = (
                    "Low quality, escalation found nothing"
                    if escalated
                    else "Low quality, no escalation geocoder configured"
                )
            else:
                final_geocode = _best(nominatim_result, escalated_result)
                confidence = final_geocode.quality_score or 0.0
                if confidence < cfg.quality_high_threshold:
                    flagged = True
                    flag_reason = "Low quality, still uncertain after escalation"

        return NormalizedAddress(
            original=original,
            pre_normalized=pre_normalized,
            geocode=final_geocode,
            confidence=round(confidence, 4),
            flagged=flagged,
            flag_reason=flag_reason,
            escalated=escalated,
            arbiter_consulted=arbiter_consulted,
            arbiter_rationale=arbiter_rationale,
        )

    async def run(
        self, on_result: Callable[[NormalizedAddress], None] | None = None
    ) -> CleaningReport:
        """Process every address. `on_result` is called after each one (for progress output)."""
        results: list[NormalizedAddress] = []

        async for raw in self._ingester.ingest():
            normalized = await self._process_one(raw.original)
            results.append(normalized)
            if on_result is not None:
                on_result(normalized)

        results = deduplicator.assign_cluster_ids(results, self._cfg.dedupe_decimal_places)

        run_id = str(uuid.uuid4())
        summary = build_summary(results)

        return CleaningReport(run_id=run_id, summary=summary, results=results)
