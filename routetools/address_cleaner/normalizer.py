from __future__ import annotations

import re
import unicodedata

from routetools.address_cleaner.config import get_settings


def normalize(address: str, abbreviations: dict[str, str] | None = None) -> str:
    """Deterministic text normalization for Spanish addresses.

    Steps:
      1. strip
      2. lowercase
      3. expand abbreviations (longest match first)
      4. NFC unicode normalization
      5. collapse whitespace
    """
    if abbreviations is None:
        abbreviations = get_settings().abbreviations

    text = address.strip().lower()

    # Sort by length descending to apply longest match first
    sorted_abbrevs = sorted(abbreviations.items(), key=lambda kv: len(kv[0]), reverse=True)
    for abbrev, expansion in sorted_abbrevs:
        # Word-boundary-aware replacement (case-insensitive already done)
        escaped = re.escape(abbrev)
        text = re.sub(escaped, expansion, text)

    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text
