"""Streamlit-free construction of the visualization dataframes.

This is the pure core that ``viz_data.py`` wraps with ``st.cache_data`` for the
Streamlit pages, and that the web API (``api/``) calls directly. It reads the
archived state from disk and returns flat pandas frames; no ``streamlit`` import.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from brand_registry import enrich_receipt_brand_columns, load_brand_directory
from data import load_reorganized_state
from models import Sidecar


def build_viz_records(output_path: Path) -> pd.DataFrame:
    """One row per archived document (multi-page docs collapsed to their first page)."""
    _tossed, accepted_metadata = load_reorganized_state(output_path)

    doc_groups: dict[str, list[tuple[str, Sidecar]]] = {}
    for fn, (sidecar, rel_path) in accepted_metadata.items():
        doc_id = sidecar.document_key or fn
        doc_groups.setdefault(doc_id, []).append((rel_path, sidecar))

    records = []
    for doc_id, pages in doc_groups.items():
        pages.sort(key=lambda x: x[0])
        first_path, first_sc = pages[0]
        paths = [p for p, _ in pages]
        review = first_sc.review
        extraction = first_sc.extraction
        ocr_parts = []
        for _, sidecar in pages:
            if sidecar.ocr and sidecar.ocr.markdown:
                ocr_parts.append(sidecar.ocr.markdown)
        items = getattr(extraction, "items", [])
        records.append({
            "filename": doc_id,
            "path": first_path,
            "paths": paths,
            "document_type": review.document_type,
            "name": review.name,
            "date": review.date,
            "time": review.time,
            "cost": float(review.cost),
            "currency": review.currency,
            "comment": review.comment,
            "address": getattr(extraction, "address", ""),
            "language": getattr(extraction, "language", ""),
            "items": [item.model_dump() for item in items] if items else [],
            "ocr_markdown": "\n\n--- Page break ---\n\n".join(ocr_parts),
        })

    df = pd.DataFrame(records)
    if not df.empty:
        df["parsed_date"] = pd.to_datetime(df["date"], format="%Y-%m-%d", errors="coerce")
        df["year"] = df["parsed_date"].dt.year
        df["month"] = df["parsed_date"].dt.month
    directory = load_brand_directory()
    df = enrich_receipt_brand_columns(df, directory)
    return df


def build_viz_items(output_path: Path) -> pd.DataFrame:
    """One row per line item across all archived receipts."""
    return viz_items_from_records(build_viz_records(output_path))


def viz_items_from_records(df: pd.DataFrame) -> pd.DataFrame:
    """Line items derived from an already-built records frame (no disk access)."""
    if df.empty:
        return pd.DataFrame()

    receipts = df[df["document_type"] == "receipt"]
    rows = []
    for _, r in receipts.iterrows():
        for item in r["items"]:
            rows.append({
                "filename": r["filename"],
                "merchant": r["name"],
                "merchant_group": r.get("merchant_group", r["name"]),
                "receipt_date": r["date"],
                "parsed_date": r["parsed_date"],
                "item_name": item.get("name", ""),
                "quantity": item.get("quantity"),
                "unit_price": item.get("unit_price"),
                "total_price": item.get("total_price"),
                "currency": r["currency"],
            })

    return pd.DataFrame(rows) if rows else pd.DataFrame()
