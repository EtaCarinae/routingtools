from __future__ import annotations

import uuid

from routetools.address_cleaner import deduplicator, normalizer
from routetools.address_cleaner.arbiter.llm_arbiter import LLMArbiter
from routetools.address_cleaner.config import AddressCleanerSettings, get_settings
from routetools.address_cleaner.geocoding import quality
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
        self._escalation_geocoder = (
            OpenCageGeocoder(
                api_key=self._cfg.opencage_api_key,
                country_code=self._cfg.country_codes,
            )
            if self._cfg.opencage_api_key
            else None
        )
        self._arbiter = (
            LLMArbiter(api_key=self._cfg.openrouter_api_key, model=self._cfg.arbiter_model)
            if self._cfg.openrouter_api_key
            else None
        )

    async def _escalate(self, address: str) -> GeocodeResult:
        if self._escalation_geocoder is None:
            return GeocodeResult(source=GeocodeSource.failed, candidate_count=0)
        return await self._escalation_geocoder.geocode(address)

    async def _process_one(self, original: str) -> NormalizedAddress:
        cfg = self._cfg
        pre_normalized = normalizer.normalize(original, cfg.abbreviations)

        nominatim_result = await self._nominatim.geocode(pre_normalized)
        q_score = quality.score(nominatim_result, cfg.bbox_ceiling_deg2)
        nominatim_result.quality_score = q_score

        final_geocode = nominatim_result
        confidence = q_score
        flagged = False
        flag_reason: str | None = None
        escalated = False
        arbiter_consulted = False
        arbiter_rationale: str | None = None

        if q_score >= cfg.quality_high_threshold:
            # Accept Nominatim directly
            pass

        elif q_score >= cfg.quality_low_threshold:
            # Grey zone — consult LLM arbiter
            arbiter_consulted = True
            if self._arbiter is not None:
                decision = await self._arbiter.arbitrate(
                    original=original,
                    pre_normalized=pre_normalized,
                    geocode_result=nominatim_result,
                    quality_score=q_score,
                    grey_zone_reason=f"Score {q_score:.3f} in grey zone "
                    f"[{cfg.quality_low_threshold}, {cfg.quality_high_threshold})",
                )
                arbiter_rationale = decision.rationale
                confidence = max(0.0, min(1.0, q_score + decision.confidence_adjustment))

                if decision.decision == "accept_nominatim":
                    pass
                elif decision.decision == "escalate_to_google":
                    escalated = True
                    escalated_result = await self._escalate(pre_normalized)
                    if escalated_result.source != GeocodeSource.failed:
                        escalated_q = quality.score(escalated_result, cfg.bbox_ceiling_deg2)
                        escalated_result.quality_score = escalated_q
                        final_geocode = escalated_result
                        confidence = escalated_q
                else:  # flag_for_review
                    flagged = True
                    flag_reason = f"Arbiter: {decision.rationale}"
            else:
                # No arbiter configured — flag automatically
                flagged = True
                flag_reason = "Grey zone, no arbiter configured"

        else:
            # Low quality — escalate directly
            escalated = True
            escalated_result = await self._escalate(pre_normalized)
            if escalated_result.source != GeocodeSource.failed:
                escalated_q = quality.score(escalated_result, cfg.bbox_ceiling_deg2)
                escalated_result.quality_score = escalated_q
                final_geocode = escalated_result
                confidence = escalated_q
            else:
                confidence = q_score
                flagged = True
                flag_reason = "Low quality, escalation also failed"

        final_geocode.needs_escalation = escalated

        return NormalizedAddress(
            original=original,
            pre_normalized=pre_normalized,
            geocode=final_geocode,
            confidence=confidence,
            flagged=flagged,
            flag_reason=flag_reason,
            escalated_to_google=escalated,
            arbiter_consulted=arbiter_consulted,
            arbiter_rationale=arbiter_rationale,
        )

    async def run(self) -> CleaningReport:
        results: list[NormalizedAddress] = []

        async for raw in self._ingester.ingest():
            normalized = await self._process_one(raw.original)
            results.append(normalized)

        results = deduplicator.assign_cluster_ids(results, self._cfg.dedupe_decimal_places)

        run_id = str(uuid.uuid4())
        summary = build_summary(results)

        return CleaningReport(run_id=run_id, summary=summary, results=results)
