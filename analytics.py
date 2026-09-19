"""Pure aggregation helpers for the visualization views.

Kept free of any web or UI framework so the computations can be unit-tested and
served by the API (``api/``); ``complete_monthly_series`` pads the series for the
monthly charts. Every function takes/returns plain pandas/Python values.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

import pandas as pd


# --- dashboard -------------------------------------------------------------
def top_merchants(receipts: pd.DataFrame, key_col: str, value_col: str, top_n: int = 20) -> pd.DataFrame:
    """Leaderboard grouped by ``key_col`` (merchant name or brand group)."""
    leaderboard = receipts.groupby(key_col, as_index=False).agg(
        total_spend=("cost", "sum"),
        visit_count=("cost", "size"),
        brand_id=("brand_id", "first"),
    )
    leaderboard["avg_per_visit"] = (
        leaderboard["total_spend"] / leaderboard["visit_count"]
    ).round().astype(int)
    return leaderboard.sort_values(value_col, ascending=False).head(top_n)


def monthly_spend(receipts: pd.DataFrame) -> pd.DataFrame:
    """Monthly total spend; columns: period, spend, month_ts."""
    dated = receipts[receipts["parsed_date"].notna()]
    if dated.empty:
        return pd.DataFrame(columns=["period", "spend", "month_ts"])
    period = dated["parsed_date"].dt.to_period("M")
    monthly = dated.groupby(period)["cost"].sum().rename("spend").reset_index()
    monthly.columns = ["period", "spend"]
    monthly["month_ts"] = monthly["period"].dt.to_timestamp()
    return monthly


def monthly_volume(documents: pd.DataFrame) -> pd.DataFrame:
    """Monthly document count; columns: period, count, month_ts."""
    dated = documents[documents["parsed_date"].notna()]
    if dated.empty:
        return pd.DataFrame(columns=["period", "count", "month_ts"])
    period = dated["parsed_date"].dt.to_period("M")
    vol = dated.groupby(period).size().rename("count").reset_index()
    vol.columns = ["period", "count"]
    vol["month_ts"] = vol["period"].dt.to_timestamp()
    return vol


def complete_monthly_series(
    monthly: pd.DataFrame,
    value_col: str,
    fill: float = 0,
    start=None,
    end=None,
) -> pd.DataFrame:
    """Give every month in the series' range a row.

    REQUIREMENT: monthly charts must preserve the passage of time. Aggregations
    only emit months that have documents, and a chart with a category x-axis then
    silently drops the empty months — e.g. a years-long pause in archiving
    collapses to nothing and the sense of time is lost. Missing months get
    ``fill`` (0 spend / 0 documents), so gaps render as empty space.

    ``start`` / ``end`` (anything ``pd.Period`` accepts) widen the range beyond
    the data — used to put side-by-side charts on one shared timeline, so the same
    month sits at the same position in each. They never narrow it.

    Takes the shape produced by ``monthly_spend`` / ``monthly_volume`` (``period``,
    ``<value_col>``, ``month_ts``) and returns the same columns, in order.
    """
    if monthly.empty:
        return monthly
    first, last = monthly["period"].min(), monthly["period"].max()
    if start is not None:
        first = min(first, pd.Period(start, freq="M"))
    if end is not None:
        last = max(last, pd.Period(end, freq="M"))
    months = pd.period_range(first, last, freq="M")
    out = (
        monthly.set_index("period")[[value_col]]
        .reindex(months, fill_value=fill)
        .rename_axis("period")
        .reset_index()
    )
    out["month_ts"] = out["period"].dt.to_timestamp()
    return out


# --- merchant profile ------------------------------------------------------
def merchant_metrics(merchant_df: pd.DataFrame) -> dict:
    """Headline stats for one merchant/brand selection."""
    dated = merchant_df[merchant_df["parsed_date"].notna()].sort_values("parsed_date")
    total_spend = merchant_df["cost"].sum()
    visit_count = len(merchant_df)
    currency_mode = merchant_df["currency"].mode()
    avg_gap = None
    if len(dated) >= 2:
        avg_gap = dated["parsed_date"].diff().dt.days.dropna().mean()
    return {
        "total_spend": total_spend,
        "visit_count": visit_count,
        "avg_ticket": total_spend / visit_count if visit_count else 0,
        "currency": currency_mode.iloc[0] if not currency_mode.empty else "",
        "first_visit": dated["parsed_date"].min() if not dated.empty else None,
        "last_visit": dated["parsed_date"].max() if not dated.empty else None,
        "avg_gap": avg_gap,
    }


def visit_cadence(dated: pd.DataFrame) -> pd.DataFrame:
    """Inter-visit gaps; columns: visit_date, days_since_last."""
    sorted_dates = dated.sort_values("parsed_date")["parsed_date"].reset_index(drop=True)
    return pd.DataFrame({
        "visit_date": sorted_dates.iloc[1:].values,
        "days_since_last": sorted_dates.diff().dt.days.iloc[1:].values,
    })


def item_breakdown(merchant_items: pd.DataFrame) -> pd.DataFrame:
    """Per-item rollup; columns: item_name, times_purchased, total_spent, avg_unit_price."""
    return merchant_items.groupby("item_name").agg(
        times_purchased=("item_name", "size"),
        total_spent=("total_price", "sum"),
        avg_unit_price=("unit_price", "mean"),
    ).reset_index()


# --- time capsule ----------------------------------------------------------
def timecapsule_matches(dated: pd.DataFrame, selected: date) -> pd.DataFrame:
    """Documents sharing ``selected``'s month+day across all years (newest first)."""
    return dated[
        (dated["parsed_date"].dt.month == selected.month)
        & (dated["parsed_date"].dt.day == selected.day)
    ].sort_values("parsed_date", ascending=False)


# --- calendar (date math + layout) ----------------------------------------
def monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def first_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def first_of_next_month(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def records_in_range(dated: pd.DataFrame, range_start: date, range_end: date) -> dict[date, list]:
    """Group records whose date falls in ``[range_start, range_end)`` by day."""
    mask = (dated["parsed_date"].dt.date >= range_start) & (
        dated["parsed_date"].dt.date < range_end
    )
    subset = dated.loc[mask]
    out: dict[date, list] = {}
    for val in subset["parsed_date"].dt.date.unique():
        d_py = val if isinstance(val, date) else date(val.year, val.month, val.day)
        out[d_py] = subset[subset["parsed_date"].dt.date == val].to_dict("records")
    return out


def balance_into_columns(
    records: list[dict], height_of: Callable[[dict], float]
) -> tuple[list[dict], list[dict]]:
    """Greedily split ``records`` into two balanced columns using ``height_of``."""
    col1, col2 = [], []
    h1, h2 = 0.0, 0.0
    for row in records:
        card_h = height_of(row)
        if h1 <= h2:
            col1.append(row)
            h1 += card_h
        else:
            col2.append(row)
            h2 += card_h
    return col1, col2
