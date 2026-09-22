"""Read-only visualization endpoints — thin wrappers over viz_records + analytics."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

import analytics
import receipt_edit
import review_logic as rl
from api import cache
from api.deps import get_output_path
from api.guards import no_job_running
from api.schemas import (
    BrandTotals, DateRange, DocumentListItem, DocumentRunsOut, FieldBoxOut, MerchantProfile, MonthlySpend,
    MonthlyVolume, NameTotals, ReceiptEditIn, ReviewPage, VizItem, VizRecord,
)
from api.serialization import df_records, to_jsonable
from data import read_sidecar

router = APIRouter(prefix="/api", tags=["visualize"])


def _records(output_path: Path, year: int | None = None):
    df = cache.viz_records(output_path)
    if df.empty or year is None:
        return df
    return df[df["year"] == year]


def _receipts(output_path: Path, year: int | None = None):
    df = _records(output_path, year)
    if df.empty:
        return df
    return df[df["document_type"] == "receipt"]


@router.get("/viz/records", response_model=list[VizRecord])
def viz_records_endpoint(output_path: Path = Depends(get_output_path)):
    return df_records(cache.viz_records(output_path))


@router.get("/viz/items", response_model=list[VizItem])
def viz_items_endpoint(output_path: Path = Depends(get_output_path)):
    return df_records(cache.viz_items(output_path))


@router.get("/analytics/top-merchants", response_model=list[BrandTotals] | list[NameTotals])
def top_merchants_endpoint(
    group_by: Literal["brand", "name"] = "brand",
    rank_by: Literal["total_spend", "visit_count"] = "total_spend",
    top_n: int = 20,
    year: int | None = None,
    output_path: Path = Depends(get_output_path),
):
    receipts = _receipts(output_path, year)
    if receipts.empty:
        return []
    key_col = "merchant_group" if group_by == "brand" else "name"
    return df_records(analytics.top_merchants(receipts, key_col, rank_by, top_n))


def _timeline(records):
    """(first, last) month of any dated document in the view — the shared
    timeline for the Dashboard's side-by-side monthly charts."""
    dated = records["parsed_date"].dropna() if not records.empty else None
    if dated is None or dated.empty:
        return None, None
    return dated.min().to_period("M"), dated.max().to_period("M")


@router.get("/analytics/monthly-spend", response_model=list[MonthlySpend])
def monthly_spend_endpoint(
    year: int | None = None,
    output_path: Path = Depends(get_output_path),
):
    records = _records(output_path, year)
    receipts = records[records["document_type"] == "receipt"] if not records.empty else records
    if receipts.empty:
        return []
    start, end = _timeline(records)
    return df_records(analytics.complete_monthly_series(
        analytics.monthly_spend(receipts), "spend", start=start, end=end))


@router.get("/analytics/monthly-volume", response_model=list[MonthlyVolume])
def monthly_volume_endpoint(
    year: int | None = None,
    output_path: Path = Depends(get_output_path),
):
    records = _records(output_path, year)
    if records.empty:
        return []
    start, end = _timeline(records)
    return df_records(analytics.complete_monthly_series(
        analytics.monthly_volume(records), "count", start=start, end=end))


@router.get("/years", response_model=list[int])
def years_endpoint(output_path: Path = Depends(get_output_path)):
    """Distinct years present in the archive, newest first."""
    df = cache.viz_records(output_path)
    if df.empty:
        return []
    years = df["year"].dropna().astype(int).unique().tolist()
    return sorted(years, reverse=True)


@router.get("/date-range", response_model=DateRange)
def date_range_endpoint(output_path: Path = Depends(get_output_path)):
    """Earliest/latest dated document — lets the UI open on a populated period
    instead of an empty current month."""
    df = cache.viz_records(output_path)
    if df.empty:
        return {"min": None, "max": None}
    dated = df["parsed_date"].dropna()
    if dated.empty:
        return {"min": None, "max": None}
    return {"min": to_jsonable(dated.min()), "max": to_jsonable(dated.max())}


@router.get("/merchants", response_model=list[BrandTotals] | list[NameTotals])
def merchants_endpoint(
    group_by: Literal["brand", "name"] = "brand",
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


@router.get("/analytics/merchant", response_model=MerchantProfile)
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
    items_df = cache.viz_items(output_path)
    merchant_items = (
        items_df[items_df["merchant"].isin(set(subset["name"]))]
        if not items_df.empty else items_df
    )
    # Slim projection for the gallery: deliberately excludes ocr_markdown/items so
    # the payload stays small for merchants with hundreds of receipts.
    slim_cols = [
        c for c in ("filename", "path", "trim", "date", "time", "cost", "currency",
                    "name", "brand_location", "document_type")
        if c in subset.columns
    ]
    gallery = subset.sort_values("parsed_date", ascending=False)[slim_cols]

    # Which branches of the brand these receipts came from (blank = the brand name with no remainder).
    locations = [{"location": value or "(no remainder)", "count": int(count)}
                 for value, count in subset["brand_location"].fillna("").value_counts().items()]

    return {
        "metrics": to_jsonable(analytics.merchant_metrics(subset)),
        "locations": locations,
        "trend": df_records(analytics.complete_monthly_series(analytics.monthly_spend(dated), "spend")),
        "cadence": df_records(analytics.visit_cadence(dated)) if len(dated) >= 3 else [],   # a gap needs a trend
        "items": df_records(analytics.item_breakdown(merchant_items)) if not merchant_items.empty else [],
        "receipts": df_records(gallery),
    }


@router.get("/analytics/timecapsule", response_model=list[VizRecord])
def timecapsule_endpoint(
    month: int = Query(..., ge=1, le=12),
    day: int = Query(..., ge=1, le=31),
    output_path: Path = Depends(get_output_path),
):
    df = cache.viz_records(output_path)
    if df.empty:
        return []
    dated = df[df["parsed_date"].notna()]
    return df_records(analytics.timecapsule_matches(dated, date(2000, month, day)))


@router.get("/analytics/calendar", response_model=dict[str, list[VizRecord]])
def calendar_endpoint(
    start: str,
    end: str,
    output_path: Path = Depends(get_output_path),
):
    try:
        range_start, range_end = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise HTTPException(status_code=400, detail="start/end must be ISO dates")
    df = cache.viz_records(output_path)
    if df.empty:
        return {}
    dated = df[df["parsed_date"].notna()]
    by_date = analytics.records_in_range(dated, range_start, range_end)
    return {d.isoformat(): [to_jsonable(r) for r in recs] for d, recs in by_date.items()}


@router.get("/documents", response_model=list[DocumentListItem])
def documents(output_path: Path = Depends(get_output_path)):
    """Every archived document, by original filename: what Receipt Detail's picker offers, including the
    undated and non-receipt documents no chart or calendar leads to."""
    df = cache.viz_records(output_path)
    if df.empty:
        return []
    listed = df[["filename", "name", "date", "time", "document_type"]].fillna("").astype(str)
    return listed.sort_values("filename").to_dict("records")


@router.get("/receipt", response_model=VizRecord)
def receipt_endpoint(
    file: str = Query(...),
    output_path: Path = Depends(get_output_path),
):
    df = cache.viz_records(output_path)
    if not df.empty:
        match = df[df["filename"] == file]
        if not match.empty:
            return to_jsonable(match.iloc[0].to_dict())
    raise HTTPException(status_code=404, detail="document not found")


@router.get("/receipt/runs", response_model=DocumentRunsOut)
def receipt_runs(file: str = Query(...), output_path: Path = Depends(get_output_path)):
    """What reading and extracting this document took, from the sidecar of its first page."""
    df = cache.viz_records(output_path)
    match = df[df["filename"] == file] if not df.empty else df
    if match.empty:
        raise HTTPException(status_code=404, detail="document not found")
    sidecar = read_sidecar(output_path / match.iloc[0]["paths"][0])
    if sidecar is None:
        raise HTTPException(status_code=404, detail="document not found")
    return DocumentRunsOut(ocr=sidecar.ocr_run, extraction=sidecar.extraction_run)


@router.get("/receipt/pages", response_model=list[ReviewPage])
def receipt_pages(file: str = Query(...), output_path: Path = Depends(get_output_path)):
    """The document's scans with their boxes, as Review and the Workshop draw them: the boxes the fields
    cite (from the extraction on the first page's sidecar), or every box read when nothing is cited, placed
    on the band each page is trimmed to. `filename` is the page's path in the archive, which is what
    /api/media/archived serves."""
    df = cache.viz_records(output_path)
    match = df[df["filename"] == file] if not df.empty else df
    if match.empty:
        raise HTTPException(status_code=404, detail="document not found")
    paths = list(match.iloc[0]["paths"])
    sidecars = [read_sidecar(output_path / rel) for rel in paths]
    extraction = sidecars[0].extraction if sidecars and sidecars[0] else None
    field_sources = getattr(extraction, "field_sources", {}) or {}
    pages = []
    for page_number, (rel, sidecar) in enumerate(zip(paths, sidecars), start=1):
        ocr = sidecar.ocr if sidecar else None
        trim = sidecar.trim if sidecar else None
        found = (ocr.boxes if ocr else None) or []
        drawn = rl.field_boxes(page_number, found, field_sources) if field_sources else rl.all_boxes(found)
        pages.append(ReviewPage(file_key=rel, filename=rel, image_available=(output_path / rel).is_file(),
                                boxes=FieldBoxOut.all_of(rl.reframed(drawn, ocr.trim if ocr else None, trim)),
                                trim=trim, retrimmed=bool(ocr and ocr.succeeded and ocr.trim != trim)))
    return pages


@router.patch("/receipt", response_model=VizRecord)
def edit_receipt_endpoint(body: ReceiptEditIn, output_path: Path = Depends(get_output_path)):
    """Change an archived document: re-file its pages under the new name and rewrite their sidecars."""
    df = cache.viz_records(output_path)
    match = df[df["filename"] == body.file] if not df.empty else df
    if match.empty:
        raise HTTPException(status_code=404, detail="document not found")
    paths = list(match.iloc[0]["paths"])

    edit = receipt_edit.ReceiptEdit(
        document_type=body.document_type, name=body.name, date=body.date, time=body.time,
        cost=body.cost, currency=body.currency, address=body.address, language=body.language,
        comment=body.comment,
    )
    try:
        # Archive moves files under the same folders; an edit must not run alongside it.
        with no_job_running("edit a document", kind="archive"):
            receipt_edit.apply_receipt_edit(output_path, paths, edit)
    except receipt_edit.NotEditable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    cache.clear()  # the archive changed under every visualize page
    return receipt_endpoint(file=body.file, output_path=output_path)
