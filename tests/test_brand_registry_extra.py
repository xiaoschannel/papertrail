"""Brand id minting, JP prefix resolution, and dataframe enrichment.

(Longest-prefix / tiebreak basics already live in test_brand_resolve.py.)"""

import pandas as pd

from brand_registry import (
    enrich_receipt_brand_columns,
    load_brand_directory,
    make_brand_id,
    resolve_brand,
)


def test_make_brand_id_slug_and_collision():
    assert make_brand_id("Tealive", set()) == "tealive"
    assert make_brand_id("Tealive", {"tealive"}) == "tealive-2"
    # non-ascii label slugs to the fallback, then disambiguates
    assert make_brand_id("セブン-イレブン", set()) == "brand"
    assert make_brand_id("セブン-イレブン", {"brand"}) == "brand-2"


def test_resolve_brand_japanese_family_both_variants(configured_archive):
    directory = load_brand_directory()
    hyphen = resolve_brand("セブン-イレブン 品川駅前店", directory)
    assert hyphen.brand_id == "seven-eleven" and hyphen.brand_location == "品川駅前店"

    no_hyphen = resolve_brand("セブンイレブン 目黒店", directory)
    assert no_hyphen.brand_id == "seven-eleven" and no_hyphen.brand_location == "目黒店"


def test_resolve_brand_unmatched(configured_archive):
    directory = load_brand_directory()
    assert resolve_brand("上海小笼包馆", directory).brand_id is None


def test_enrich_receipt_brand_columns(configured_archive):
    directory = load_brand_directory()
    df = pd.DataFrame([
        {"document_type": "receipt", "name": "Tealive KLCC"},
        {"document_type": "receipt", "name": "上海小笼包馆"},
        {"document_type": "other", "name": "ATM明細"},
    ])
    out = enrich_receipt_brand_columns(df, directory)
    assert out.loc[0, "brand_id"] == "tealive"
    assert out.loc[0, "merchant_group"] == "Tealive"
    # Assert "no brand matched" semantically, not by identity: pandas 2 keeps the
    # None we assign, pandas 3 surfaces it as NaN. Callers already treat both the
    # same (pd.notna guards in the pages).
    # unmatched receipt groups under its own name
    assert pd.isna(out.loc[1, "brand_id"])
    assert out.loc[1, "merchant_group"] == "上海小笼包馆"
    # non-receipt rows are left unbranded
    assert pd.isna(out.loc[2, "brand_id"])
    assert out.loc[2, "merchant_group"] == ""
