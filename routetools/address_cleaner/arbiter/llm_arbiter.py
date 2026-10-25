from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from openai import AsyncOpenAI, AuthenticationError, PermissionDeniedError, RateLimitError

from routetools.address_cleaner.errors import ProviderAuthError
from routetools.address_cleaner.models import GeocodeResult

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

DECISIONS = ("accept", "escalate", "flag_for_review")

# The LLM can nudge the deterministic score, not replace it.
MAX_CONFIDENCE_ADJUSTMENT = 0.2

_DECISION_FUNCTION = {
    "name": "address_decision",
    "description": "Decide how to handle a geocoding result in the grey zone.",
    "parameters": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": list(DECISIONS),
                "description": "The arbiter's decision.",
            },
            "rationale": {
                "type": "string",
                "description": "One short sentence, in Spanish, explaining the decision.",
            },
            "confidence_adjustment": {
                "type": "number",
                "description": (
                    "Signed adjustment to the quality score, between "
                    f"-{MAX_CONFIDENCE_ADJUSTMENT} and {MAX_CONFIDENCE_ADJUSTMENT}."
                ),
            },
        },
        "required": ["decision", "rationale", "confidence_adjustment"],
    },
}

_SYSTEM_PROMPT = """You review geocoding results for Spanish delivery addresses.
A deterministic scorer could not decide whether the result is good enough, so you decide:
- accept: the result clearly refers to the same place the input describes.
- escalate: the result looks wrong or imprecise and a second geocoder may do better
  (e.g. it landed in a different town than the one the input names, or only matched the street).
- flag_for_review: the input itself is ambiguous or incomplete; a person must check it.
Compare street, house number and locality between input and result. Be conservative:
a wrong delivery point costs more than a manual review."""

_RETRY_DELAYS = [2.0, 5.0, 10.0]  # seconds between retries


@dataclass(frozen=True)
class ArbiterDecision:
    decision: str
    rationale: str
    confidence_adjustment: float


def _flag(rationale: str) -> ArbiterDecision:
    return ArbiterDecision(
        decision="flag_for_review", rationale=rationale, confidence_adjustment=0.0
    )


def build_prompt(
    original: str,
    pre_normalized: str,
    geocode_result: GeocodeResult,
    quality_score: float,
    expected_locality: str | None,
) -> str:
    r = geocode_result
    return (
        f"Input address: {original!r}\n"
        f"Normalized: {pre_normalized!r}\n"
        f"Locality named in the input: {expected_locality or 'none'}\n\n"
        f"Geocoder ({r.source.value}) result: {r.formatted_address!r}\n"
        f"Result locality: {r.locality or 'unknown'}\n"
        f"Precision: {r.address_type.value}; "
        f"house number resolved: {'yes' if r.house_number_resolved else 'no'}\n"
        f"Candidates at distinct locations: {r.candidate_count}\n"
        f"Quality score: {quality_score:.2f}\n"
    )


def parse_decision(arguments: str) -> ArbiterDecision:
    """Validate the tool-call arguments; anything malformed becomes a flag."""
    try:
        args = json.loads(arguments)
        decision = args["decision"]
        rationale = str(args["rationale"])
        adjustment = float(args.get("confidence_adjustment", 0.0))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return _flag("LLM returned a malformed decision.")
    if decision not in DECISIONS:
        return _flag(f"LLM returned an unknown decision: {decision!r}.")
    adjustment = max(-MAX_CONFIDENCE_ADJUSTMENT, min(MAX_CONFIDENCE_ADJUSTMENT, adjustment))
    return ArbiterDecision(decision, rationale, adjustment)


class LLMArbiter:
    def __init__(
        self,
        api_key: str,
        model: str = "mistralai/mistral-small-3.2-24b-instruct",
        base_url: str = _OPENROUTER_BASE_URL,
    ) -> None:
        # Retries are handled below (_RETRY_DELAYS), not by the SDK
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)
        self._model = model

    async def arbitrate(
        self,
        original: str,
        pre_normalized: str,
        geocode_result: GeocodeResult,
        quality_score: float,
        expected_locality: str | None = None,
    ) -> ArbiterDecision:
        prompt = build_prompt(
            original, pre_normalized, geocode_result, quality_score, expected_locality
        )

        last_error: str = ""
        for attempt, delay in enumerate([0.0] + _RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    max_tokens=1024,  # reasoning models spend tokens before the tool call
                    temperature=0,
                    tools=[{"type": "function", "function": _DECISION_FUNCTION}],
                    tool_choice={"type": "function", "function": {"name": "address_decision"}},
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                )
                choices = response.choices or []
                tool_calls = choices[0].message.tool_calls if choices else None
                if tool_calls:
                    return parse_decision(tool_calls[0].function.arguments)
                return _flag("LLM did not return a structured decision.")
            except (AuthenticationError, PermissionDeniedError) as exc:
                raise ProviderAuthError(
                    f"OpenRouter rejected the API key (HTTP {exc.status_code}). "
                    "Check OPENROUTER_API_KEY."
                ) from None
            except RateLimitError as exc:
                last_error = f"Rate limited (attempt {attempt + 1}): {exc}"
                continue
            except Exception as exc:
                last_error = str(exc)
                break

        # Retries exhausted or hard error: degrade to human review instead of crashing.
        return _flag(f"Arbiter unavailable: {last_error}")
