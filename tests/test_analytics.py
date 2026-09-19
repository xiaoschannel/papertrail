"""Pure visualization aggregations (extracted from the Streamlit pages)."""

from datetime import date

import pandas as pd

from analytics import (
    balance_into_columns,
    first_of_next_month,
    merchant_metrics,
    monday_of_week,
    monthly_spend,
    records_in_range,
    timecapsule_matches,
    top_merchants,
)
from viz_records import build_viz_records


def _receipts(configured_archive) -> pd.DataFrame:
    df = build_viz_records(configured_archive)
    return df[df["document_type"] == "receipt"]


def test_top_merchants_ranked_by_spend(configured_archive):
    receipts = _receipts(configured_archive)
    lb = top_merchants(receipts, "merchant_group", "total_spend")
    top = lb.iloc[0]
    assert top["merchant_group"] == "ビックカメラ" and top["total_spend"] == 98000.0

    seven = lb[lb["merchant_group"] == "セブン-イレブン"].iloc[0]
    assert seven["total_spend"] == 2880.0   # 1260 + 640 + 980
    assert seven["visit_count"] == 3


def test_monthly_spend_buckets_by_month(configured_archive):
    receipts = _receipts(configured_archive)
    monthly = monthly_spend(receipts)
    jan = monthly[monthly["month_ts"] == pd.Timestamp("2025-01-01")].iloc[0]
    assert jan["spend"] == 1900.0   # 品川 1260 + 上野 640


def test_merchant_metrics(configured_archive):
    receipts = _receipts(configured_archive)
    seven = receipts[receipts["merchant_group"] == "セブン-イレブン"]
    m = merchant_metrics(seven)
    assert m["visit_count"] == 3
    assert m["total_spend"] == 2880.0
    assert round(m["avg_ticket"]) == 960
    assert m["currency"] == "JPY"
    assert m["avg_gap"] is not None


def test_timecapsule_matches_same_month_day_across_years(configured_archive):
    df = build_viz_records(configured_archive)
    dated = df[df["parsed_date"].notna()]
    matches = timecapsule_matches(dated, date(2024, 11, 3))
    names = set(matches["name"])
    assert names == {"上海小笼包馆", "スターバックス 渋谷店"}  # 2024 + 2023, Nov 3


def test_records_in_range_and_column_balance():
    df = pd.DataFrame({
        "parsed_date": pd.to_datetime(["2025-01-01", "2025-01-01", "2025-01-05", "2025-02-01"]),
        "name": ["a", "b", "c", "d"],
    })
    grouped = records_in_range(df, date(2025, 1, 1), date(2025, 2, 1))
    assert set(grouped) == {date(2025, 1, 1), date(2025, 1, 5)}
    assert len(grouped[date(2025, 1, 1)]) == 2

    col1, col2 = balance_into_columns([{"x": 1}, {"x": 1}, {"x": 1}], height_of=lambda r: 1.0)
    assert len(col1) == 2 and len(col2) == 1  # greedy: shorter column gets the tie


def test_complete_monthly_series_fills_gaps_with_zero():
    from analytics import complete_monthly_series

    monthly = pd.DataFrame({
        "period": pd.PeriodIndex(["2025-01", "2025-04"], freq="M"),
        "spend": [100.0, 40.0],
    })
    monthly["month_ts"] = monthly["period"].dt.to_timestamp()

    out = complete_monthly_series(monthly, "spend")
    assert [str(p) for p in out["period"]] == ["2025-01", "2025-02", "2025-03", "2025-04"]
    assert list(out["spend"]) == [100.0, 0.0, 0.0, 40.0]
    assert list(out.columns) == ["period", "spend", "month_ts"]
    assert out["month_ts"].iloc[1] == pd.Timestamp("2025-02-01")


def test_complete_monthly_series_start_end_widen_never_narrow():
    from analytics import complete_monthly_series

    monthly = pd.DataFrame({"period": pd.PeriodIndex(["2025-03"], freq="M"), "count": [5]})
    monthly["month_ts"] = monthly["period"].dt.to_timestamp()

    widened = complete_monthly_series(monthly, "count", start="2025-01", end="2025-04")
    assert [str(p) for p in widened["period"]] == ["2025-01", "2025-02", "2025-03", "2025-04"]
    assert list(widened["count"]) == [0, 0, 5, 0]

    # a start/end inside the data range must not cut data off
    kept = complete_monthly_series(monthly, "count", start="2025-03", end="2025-03")
    assert list(kept["count"]) == [5]


def test_complete_monthly_series_empty_passthrough():
    from analytics import complete_monthly_series

    empty = pd.DataFrame(columns=["period", "spend", "month_ts"])
    assert complete_monthly_series(empty, "spend").empty


def test_calendar_date_helpers():
    assert monday_of_week(date(2025, 1, 8)) == date(2025, 1, 6)   # Wed -> Mon
    assert first_of_next_month(date(2025, 12, 15)) == date(2026, 1, 1)
