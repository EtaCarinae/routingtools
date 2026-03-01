from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_SETTINGS_YAML = Path(__file__).parent.parent.parent / "config" / "settings.yaml"


def _load_yaml_defaults() -> dict[str, Any]:
    if _SETTINGS_YAML.exists():
        with _SETTINGS_YAML.open() as f:
            data = yaml.safe_load(f) or {}
        return data.get("address_cleaner", {})
    return {}


_yaml = _load_yaml_defaults()


class AddressCleanerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    # Geocoding
    nominatim_user_agent: str = Field(
        default="routetools/1.0 (user@example.com)",
        alias="NOMINATIM_USER_AGENT",
    )
    google_maps_api_key: str = Field(default="", alias="GOOGLE_MAPS_API_KEY")
    opencage_api_key: str = Field(default="", alias="OPENCAGE_API_KEY")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")

    # Tuning — loaded from YAML with fallback defaults
    country_codes: str = _yaml.get("country_codes", "es")
    quality_high_threshold: float = _yaml.get("quality_high_threshold", 0.80)
    quality_low_threshold: float = _yaml.get("quality_low_threshold", 0.50)
    nominatim_rps: float = _yaml.get("nominatim_rps", 1.0)
    bbox_ceiling_deg2: float = _yaml.get("bbox_ceiling_deg2", 0.01)
    dedupe_decimal_places: int = _yaml.get("dedupe_decimal_places", 5)
    arbiter_model: str = _yaml.get("arbiter_model", "meta-llama/llama-3.3-70b-instruct:free")
    abbreviations: dict[str, str] = _yaml.get(
        "abbreviations",
        {
            "c/": "calle",
            "avda.": "avenida",
            "av.": "avenida",
            "pº": "paseo",
            "ctra.": "carretera",
            "s/n": "sin número",
            "pza.": "plaza",
            "pol.": "poligono",
            "urb.": "urbanizacion",
        },
    )


_settings: AddressCleanerSettings | None = None


def get_settings() -> AddressCleanerSettings:
    global _settings
    if _settings is None:
        _settings = AddressCleanerSettings()
    return _settings
