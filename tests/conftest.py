import pytest


@pytest.fixture
def sample_csv_path():
    from pathlib import Path

    return Path(__file__).parent / "address_cleaner" / "fixtures" / "sample_addresses.csv"
