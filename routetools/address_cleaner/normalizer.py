from __future__ import annotations

import re
import unicodedata

from routetools.address_cleaner.config import get_settings


def normalize(address: str, abbreviations: dict[str, str] | None = None) -> str:
    """Deterministic text normalization for Spanish addresses.

    Steps:
      1. strip
      2. lowercase
      3. expand abbreviations (longest match first, only at the start of a word)
      4. NFC unicode normalization
      5. collapse whitespace (and drop spaces before commas)
    """
    if abbreviations is None:
        abbreviations = get_settings().abbreviations

    text = address.strip().lower()

    # Longest first so "avda." is expanded before "av." gets a chance to match.
    sorted_abbrevs = sorted(abbreviations.items(), key=lambda kv: len(kv[0]), reverse=True)
    for abbrev, expansion in sorted_abbrevs:
        # Not preceded by a letter/digit: "av." must not match inside "nav." (nave).
        text = re.sub(rf"(?<!\w){re.escape(abbrev)}", expansion, text)

    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r" ,", ",", text)

    return text


def fold(text: str) -> str:
    """Accent- and case-insensitive form for comparisons ("València" == "valencia")."""
    decomposed = unicodedata.normalize("NFD", text.strip().lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def extract_locality(address: str) -> str | None:
    """The locality the input names: its last comma-separated part, if it has no digits.

    "calle mayor 10, madrid" → "madrid"; "calle mayor 10" → None;
    "calle mayor 10, 28013" → None (a postcode is not a locality).
    """
    parts = [p.strip() for p in address.split(",")]
    if len(parts) < 2:
        return None
    last = parts[-1]
    if not last or any(c.isdigit() for c in last):
        return None
    return last
