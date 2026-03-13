from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st

from data import load_reorganized_state
from settings import get_config


def get_output_path() -> Path | None:
    cfg = get_config()
    batch_dir = cfg.get("batch_output_path", "")
    if not batch_dir:
        return None
    return Path(batch_dir)


def merchant_url(name: str) -> str:
    return f"/merchant?name={quote(name)}"


def receipt_url(filename: str) -> str:
    return f"/receipt?file={quote(filename)}"


def sync_query_param(param_name: str, widget_key: str, valid_values: list[str]):
    param_value = st.query_params.get(param_name, "")
    tracker_key = f"_qp_{widget_key}"
    if param_value and param_value in valid_values and st.session_state.get(tracker_key) != param_value:
        st.session_state[widget_key] = param_value
        st.session_state[tracker_key] = param_value


@st.cache_data(ttl=120)
def load_viz_records(output_path_str: str) -> pd.DataFrame:
    output_path = Path(output_path_str)
    _tossed, accepted_metadata = load_reorganized_state(output_path)

    doc_groups: dict[str, list[tuple[str, dict]]] = {}
    for fn, entry in accepted_metadata.items():
        doc_key = entry.get("document_key")
        doc_id = doc_key if doc_key else fn
        path = entry.get("_path", "")
        doc_groups.setdefault(doc_id, []).append((path, entry))

    records = []
    for doc_id, pages in doc_groups.items():
        pages.sort(key=lambda x: x[0])
        first_path, first_entry = pages[0]
        paths = [p for p, _ in pages]
        review = first_entry.get("review", {})
        extraction = first_entry.get("extraction") or {}
        ocr_parts = []
        for _, ent in pages:
            md = ent.get("ocr", {}).get("markdown", "")
            if md:
                ocr_parts.append(md)
        records.append({
            "filename": doc_id,
            "path": first_path,
            "paths": paths,
            "document_type": review.get("document_type", ""),
            "name": review.get("name", ""),
            "date": review.get("date", ""),
            "time": review.get("time", ""),
            "cost": float(review.get("cost", 0)),
            "currency": review.get("currency", ""),
            "location": extraction.get("location", ""),
            "language": extraction.get("language", ""),
            "items": extraction.get("items", []),
            "ocr_markdown": "\n\n--- Page break ---\n\n".join(ocr_parts),
        })

    df = pd.DataFrame(records)
    if not df.empty:
        df["parsed_date"] = pd.to_datetime(df["date"], format="%Y-%m-%d", errors="coerce")
        df["year"] = df["parsed_date"].dt.year
        df["month"] = df["parsed_date"].dt.month
    return df


@st.cache_data(ttl=120)
def load_viz_items(output_path_str: str) -> pd.DataFrame:
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
                "receipt_date": r["date"],
                "parsed_date": r["parsed_date"],
                "item_name": item.get("name", ""),
                "quantity": item.get("quantity"),
                "unit_price": item.get("unit_price"),
                "total_price": item.get("total_price"),
                "currency": r["currency"],
            })

    return pd.DataFrame(rows) if rows else pd.DataFrame()
