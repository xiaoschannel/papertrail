"""The visualization dataframes built from the archive."""

import pandas as pd

from viz_records import build_viz_items, build_viz_records


def test_build_viz_records_collapses_multipage(configured_archive):
    df = build_viz_records(configured_archive)
    # 9 archived sidecars, but the 2-page ビックカメラ doc collapses to one record.
    assert len(df) == 8

    multi = df[df["filename"] == "5:106-107"].iloc[0]
    assert len(multi["paths"]) == 2
    assert "--- Page break ---" in multi["ocr_markdown"]


def test_build_viz_records_brand_and_dates(configured_archive):
    df = build_viz_records(configured_archive).set_index("filename")

    seven = df.loc["01102025132642_101.png"]
    assert seven["merchant_group"] == "セブン-イレブン"   # brand matched
    assert seven["year"] == 2025

    unmatched = df.loc["11032024121500_104.png"]
    assert unmatched["merchant_group"] == "上海小笼包馆"   # falls back to name

    undated = df.loc["01012025142000_108.png"]
    assert pd.isna(undated["parsed_date"])               # undated 'other' doc


def test_build_viz_items(configured_archive):
    items = build_viz_items(configured_archive)
    # 品川(2) + 上野(1) + 目黒(1) + 上海(2) + Tealive(1) + ビック(1) + スタバ(1) = 9
    assert len(items) == 9
    assert set(items.columns) >= {"merchant", "merchant_group", "item_name", "total_price"}
