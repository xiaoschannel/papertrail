from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st

from brand_registry import brand_registry_mtime
from settings import get_config
from viz_records import build_viz_records, viz_items_from_records


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
    return build_viz_records(Path(output_path_str))


def clear_viz_data_cache() -> None:
    _load_viz_records_cached.clear()
    _load_viz_items_cached.clear()


def load_viz_items(output_path_str: str) -> pd.DataFrame:
    mt = brand_registry_mtime()
    return _load_viz_items_cached(output_path_str, mt)


@st.cache_data(ttl=120)
def _load_viz_items_cached(output_path_str: str, brand_registry_mtime: float) -> pd.DataFrame:
    _ = brand_registry_mtime
    # Derive from the cached records frame so a cold items cache doesn't re-read the archive.
    return viz_items_from_records(load_viz_records(output_path_str))
