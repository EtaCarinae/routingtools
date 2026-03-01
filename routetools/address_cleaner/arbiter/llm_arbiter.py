from __future__ import annotations

import asyncio
import json

from openai import AsyncOpenAI, RateLimitError

from routetools.address_cleaner.models import GeocodeResult

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_DECISION_FUNCTION = {
    "name": "address_decision",
    "description": "Decide how to handle a geocoding result in the grey zone.",
    "parameters": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["accept_nominatim", "escalate_to_google", "flag_for_review"],
                "description": "The arbiter's decision.",
            },
            "rationale": {
                "type": "string",
                "description": "Brief explanation of the decision.",
            },
            "confidence_adjustment": {
                "type": "number",
                "description": "Signed float to adjust the quality score (e.g. -0.1).",
            },
        },
        "required": ["decision", "rationale", "confidence_adjustment"],
    },
}

_RETRY_DELAYS = [2.0, 5.0, 10.0]  # seconds between retries


class ArbiterDecision:
    def __init__(self, decision: str, rationale: str, confidence_adjustment: float) -> None:
        self.decision = decision
        self.rationale = rationale
        self.confidence_adjustment = confidence_adjustment


class LLMArbiter:
    def __init__(
        self,
        api_key: str,
        model: str = "meta-llama/llama-3.3-70b-instruct:free",
        base_url: str = _OPENROUTER_BASE_URL,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def arbitrate(
        self,
        original: str,
        pre_normalized: str,
        geocode_result: GeocodeResult,
        quality_score: float,
        grey_zone_reason: str,
    ) -> ArbiterDecision:
        prompt = (
            f"You are a Spanish address quality arbiter.\n\n"
            f"Original address: {original!r}\n"
            f"Pre-normalized: {pre_normalized!r}\n"
            f"Geocoder result: {geocode_result.formatted_address!r}\n"
            f"Source: {geocode_result.source.value}\n"
            f"Quality score: {quality_score:.3f}\n"
            f"Grey zone reason: {grey_zone_reason}\n\n"
            f"Decide whether to:\n"
            f"- accept_nominatim: the result is good enough\n"
            f"- escalate_to_google: try OpenCage for a better result\n"
            f"- flag_for_review: the address needs human review\n"
        )

        last_error: str = ""
        for attempt, delay in enumerate([0.0] + _RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    max_tokens=256,
                    tools=[{"type": "function", "function": _DECISION_FUNCTION}],
                    tool_choice={"type": "function", "function": {"name": "address_decision"}},
                    messages=[{"role": "user", "content": prompt}],
                )
                choices = response.choices or []
                tool_calls = choices[0].message.tool_calls if choices else None
                if tool_calls:
                    args = json.loads(tool_calls[0].function.arguments)
                    return ArbiterDecision(
                        decision=args["decision"],
                        rationale=args["rationale"],
                        confidence_adjustment=float(args.get("confidence_adjustment", 0.0)),
                    )
                # Model responded but without a tool call
                return ArbiterDecision(
                    decision="flag_for_review",
                    rationale="LLM did not return a structured decision.",
                    confidence_adjustment=0.0,
                )
            except RateLimitError as exc:
                last_error = f"Rate limited (attempt {attempt + 1}): {exc}"
                continue
            except Exception as exc:
                last_error = str(exc)
                break

        # All retries exhausted — flag gracefully instead of crashing
        return ArbiterDecision(
            decision="flag_for_review",
            rationale=f"Arbiter unavailable after retries: {last_error}",
            confidence_adjustment=0.0,
        )
