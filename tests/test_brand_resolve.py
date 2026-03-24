import pytest

from brand_registry import BrandDirectory, BrandEntry, resolve_brand


def test_resolve_brand_empty_name_returns_empty_result():
    directory = BrandDirectory(brands=[BrandEntry(id="a", label="A", prefixes=["a"])])
    result = resolve_brand("", directory)
    assert result.brand_id is None
    assert result.brand_label is None
    assert result.matched_prefix is None
    assert result.brand_location == ""


def test_resolve_brand_empty_directory_returns_empty_result():
    result = resolve_brand("seven-eleven #123", BrandDirectory(brands=[]))
    assert result.brand_id is None
    assert result.matched_prefix is None


@pytest.mark.parametrize(
    "merchant,prefixes,expected_prefix",
    [
        ("SEVEN-ELEVEN #123", ["seven-elev", "seven-eleven"], "seven-eleven"),
        ("Starbucks 001", ["star", "starbucks"], "starbucks"),
    ],
)
def test_resolve_brand_longest_prefix_wins_case_insensitive(merchant, prefixes, expected_prefix):
    directory = BrandDirectory(
        brands=[BrandEntry(id="brand", label="Brand", prefixes=prefixes)]
    )
    result = resolve_brand(merchant, directory)
    assert result.brand_id == "brand"
    assert result.matched_prefix == expected_prefix


def test_resolve_brand_equal_length_tie_break_uses_registry_order():
    directory = BrandDirectory(
        brands=[
            BrandEntry(id="first", label="First", prefixes=["mart"]),
            BrandEntry(id="second", label="Second", prefixes=["mart"]),
        ]
    )
    result = resolve_brand("mart central", directory)
    assert result.brand_id == "first"


def test_resolve_brand_trims_prefix_and_extracts_location():
    directory = BrandDirectory(
        brands=[BrandEntry(id="seven", label="Seven Eleven", prefixes=[" seven-eleven "])]
    )
    result = resolve_brand("seven-eleven KLCC", directory)
    assert result.brand_id == "seven"
    assert result.brand_location == "KLCC"
