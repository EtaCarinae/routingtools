import pytest

from routetools.address_cleaner.normalizer import extract_locality, fold, normalize

_ABBREVS = {
    "c/": "calle",
    "avda.": "avenida",
    "av.": "avenida",
    "pº": "paseo",
    "ctra.": "carretera",
    "s/n": "",
    "pza.": "plaza",
    "pol.": "poligono",
    "urb.": "urbanizacion",
}


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("C/ Mayor 10, Madrid", "calle mayor 10, madrid"),
        ("Avda. de la Constitución 5, Sevilla", "avenida de la constitución 5, sevilla"),
        ("Pza. del Pilar s/n, Zaragoza", "plaza del pilar, zaragoza"),
        ("Ctra. Nacional II km 15", "carretera nacional ii km 15"),
        ("Urb. Las Rosas Av. del Parque 3", "urbanizacion las rosas avenida del parque 3"),
        ("  Gran  Vía   28  ", "gran vía 28"),
        ("Pol. Industrial Norte", "poligono industrial norte"),
    ],
)
def test_normalize_abbreviations(raw, expected):
    result = normalize(raw, abbreviations=_ABBREVS)
    assert result == expected


def test_normalize_unicode_nfc():
    # Precomposed vs decomposed é
    decomposed = "calle cafe\u0301 1"
    result = normalize(decomposed, abbreviations={})
    import unicodedata

    assert unicodedata.is_normalized("NFC", result)


def test_normalize_collapses_whitespace():
    result = normalize("  calle   mayor   10  ", abbreviations={})
    assert result == "calle mayor 10"


def test_normalize_lowercases():
    result = normalize("GRAN VIA 28", abbreviations={})
    assert result == "gran via 28"


def test_abbreviation_only_at_word_start():
    # "av." inside "nav." (nave) must not become "navenida"
    assert normalize("pol. norte, nav. 4", abbreviations={"av.": "avenida"}) == "pol. norte, nav. 4"
    assert normalize("av. del parque 3", abbreviations={"av.": "avenida"}) == "avenida del parque 3"


@pytest.mark.parametrize(
    "address, locality",
    [
        ("calle mayor 10, madrid", "madrid"),
        ("urbanizacion las rosas, avenida del parque 3, valencia", "valencia"),
        ("calle mayor 10", None),
        ("calle mayor 10, 28013", None),
        ("calle mayor 10, ", None),
    ],
)
def test_extract_locality(address, locality):
    assert extract_locality(address) == locality


def test_fold_ignores_accents_and_case():
    assert fold("València") == fold("valencia") == "valencia"
