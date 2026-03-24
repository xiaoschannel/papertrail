from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st

from brand_registry import brand_registry_mtime, enrich_receipt_brand_columns, load_brand_directory
from data import load_reorganized_state
from models import Sidecar
from settings import get_config


def get_output_path() -> Path | None:
    cfg = get_config()
    batch_dir = cfg.batch_output_path
    if not batch_dir:
        return None
    return Path(batch_dir)


def merchant_url(name: str | None = None, *, brand_id: str | None = None) -> str:
    if brand_id:
        return f"/merchant?brand={quote(brand_id)}"
    if name is not None:
        return f"/merchant?name={quote(name)}"
    return "/merchant"


def receipt_url(filename: str) -> str:
    return f"/receipt?file={quote(filename)}"


def sync_query_param(param_name: str, widget_key: str, valid_values: list[str]):
    param_value = st.query_params.get(param_name, "")
    tracker_key = f"_qp_{widget_key}"
    if param_value and param_value in valid_values and st.session_state.get(tracker_key) != param_value:
        st.session_state[widget_key] = param_value
        st.session_state[tracker_key] = param_value


def load_viz_records(output_path_str: str) -> pd.DataFrame:
    mt = brand_registry_mtime()
    return _load_viz_records_cached(output_path_str, mt)


@st.cache_data(ttl=120)
def _load_viz_records_cached(output_path_str: str, brand_registry_mtime: float) -> pd.DataFrame:
    _ = brand_registry_mtime
    output_path = Path(output_path_str)
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


def clear_viz_data_cache() -> None:
    _load_viz_records_cached.clear()
    _load_viz_items_cached.clear()


def load_viz_items(output_path_str: str) -> pd.DataFrame:
    mt = brand_registry_mtime()
    return _load_viz_items_cached(output_path_str, mt)


@st.cache_data(ttl=120)
def _load_viz_items_cached(output_path_str: str, brand_registry_mtime: float) -> pd.DataFrame:
    _ = brand_registry_mtime
    df = load_viz_records(output_path_str)
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
