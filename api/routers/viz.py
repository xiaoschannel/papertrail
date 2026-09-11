"""Read-only visualization endpoints — thin wrappers over viz_records + analytics."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

import analytics
from api.deps import get_output_path
from api.serialization import df_records, to_jsonable
from viz_records import build_viz_items, build_viz_records

router = APIRouter(prefix="/api", tags=["visualize"])


def _records(output_path: Path, year: int | None = None):
    df = build_viz_records(output_path)
    if df.empty or year is None:
        return df
    return df[df["year"] == year]


def _receipts(output_path: Path, year: int | None = None):
    df = _records(output_path, year)
    if df.empty:
        return df
    return df[df["document_type"] == "receipt"]


@router.get("/viz/records")
def viz_records_endpoint(output_path: Path = Depends(get_output_path)):
    return df_records(build_viz_records(output_path))


@router.get("/viz/items")
def viz_items_endpoint(output_path: Path = Depends(get_output_path)):
    return df_records(build_viz_items(output_path))


@router.get("/analytics/top-merchants")
def top_merchants_endpoint(
    group_by: str = Query("brand", pattern="^(brand|name)$"),
    rank_by: str = Query("total_spend", pattern="^(total_spend|visit_count)$"),
    top_n: int = 20,
    year: int | None = None,
    output_path: Path = Depends(get_output_path),
):
    receipts = _receipts(output_path, year)
    if receipts.empty:
        return []
    key_col = "merchant_group" if group_by == "brand" else "name"
    return df_records(analytics.top_merchants(receipts, key_col, rank_by, top_n))


@router.get("/analytics/monthly-spend")
def monthly_spend_endpoint(
    year: int | None = None,
    output_path: Path = Depends(get_output_path),
):
    receipts = _receipts(output_path, year)
    return df_records(analytics.monthly_spend(receipts)) if not receipts.empty else []


@router.get("/analytics/monthly-volume")
def monthly_volume_endpoint(
    year: int | None = None,
    output_path: Path = Depends(get_output_path),
):
    df = _records(output_path, year)
    return df_records(analytics.monthly_volume(df)) if not df.empty else []


@router.get("/years")
def years_endpoint(output_path: Path = Depends(get_output_path)):
    """Distinct years present in the archive, newest first."""
    df = build_viz_records(output_path)
    if df.empty:
        return []
    years = df["year"].dropna().astype(int).unique().tolist()
    return sorted(years, reverse=True)


@router.get("/date-range")
def date_range_endpoint(output_path: Path = Depends(get_output_path)):
    """Earliest/latest dated document — lets the UI open on a populated period
    instead of an empty current month."""
    df = build_viz_records(output_path)
    if df.empty:
        return {"min": None, "max": None}
    dated = df["parsed_date"].dropna()
    if dated.empty:
        return {"min": None, "max": None}
    return {"min": to_jsonable(dated.min()), "max": to_jsonable(dated.max())}


@router.get("/merchants")
def merchants_endpoint(
    group_by: str = Query("brand", pattern="^(brand|name)$"),
    output_path: Path = Depends(get_output_path),
):
    """Every merchant/brand with totals — the selector list for the merchant page.

    Deliberately excludes the heavy per-document fields so the UI never has to
    pull the full records payload just to populate a dropdown.
    """
    receipts = _receipts(output_path)
    if receipts.empty:
        return []
    key_col = "merchant_group" if group_by == "brand" else "name"
    lb = analytics.top_merchants(receipts, key_col, "total_spend", top_n=1_000_000)
    return df_records(lb)


@router.get("/analytics/merchant")
def merchant_endpoint(
    name: str | None = None,
    brand_id: str | None = None,
    output_path: Path = Depends(get_output_path),
):
    if not name and not brand_id:
        raise HTTPException(status_code=400, detail="provide name or brand_id")
    receipts = _receipts(output_path)
    if receipts.empty:
        raise HTTPException(status_code=404, detail="no receipts")
    subset = receipts[receipts["brand_id"] == brand_id] if brand_id else receipts[receipts["name"] == name]
    if subset.empty:
        raise HTTPException(status_code=404, detail="no receipts for selection")

    dated = subset[subset["parsed_date"].notna()].sort_values("parsed_date")
    items_df = build_viz_items(output_path)
    merchant_items = (
        items_df[items_df["merchant"].isin(set(subset["name"]))]
        if not items_df.empty else items_df
    )
    # Slim projection for the gallery: deliberately excludes ocr_markdown/items so
    # the payload stays small for merchants with hundreds of receipts.
    slim_cols = [
        c for c in ("filename", "path", "date", "time", "cost", "currency",
                    "name", "brand_location", "document_type")
        if c in subset.columns
    ]
    gallery = subset.sort_values("parsed_date", ascending=False)[slim_cols]

    return {
        "metrics": to_jsonable(analytics.merchant_metrics(subset)),
        "trend": df_records(analytics.monthly_spend(dated)),
        "cadence": df_records(analytics.visit_cadence(dated)) if len(dated) >= 2 else [],
        "items": df_records(analytics.item_breakdown(merchant_items)) if not merchant_items.empty else [],
        "receipts": df_records(gallery),
    }


@router.get("/analytics/timecapsule")
def timecapsule_endpoint(
    month: int = Query(..., ge=1, le=12),
    day: int = Query(..., ge=1, le=31),
    output_path: Path = Depends(get_output_path),
):
    df = build_viz_records(output_path)
    if df.empty:
        return []
    dated = df[df["parsed_date"].notna()]
    return df_records(analytics.timecapsule_matches(dated, date(2000, month, day)))


@router.get("/analytics/calendar")
def calendar_endpoint(
    start: str,
    end: str,
    output_path: Path = Depends(get_output_path),
):
    try:
        range_start, range_end = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise HTTPException(status_code=400, detail="start/end must be ISO dates")
    df = build_viz_records(output_path)
    if df.empty:
        return {}
    dated = df[df["parsed_date"].notna()]
    by_date = analytics.records_in_range(dated, range_start, range_end)
    return {d.isoformat(): [to_jsonable(r) for r in recs] for d, recs in by_date.items()}


@router.get("/receipt")
def receipt_endpoint(
    file: str = Query(...),
    output_path: Path = Depends(get_output_path),
):
    df = build_viz_records(output_path)
    if not df.empty:
        match = df[df["filename"] == file]
        if not match.empty:
            return to_jsonable(match.iloc[0].to_dict())
    raise HTTPException(status_code=404, detail="document not found")
