"""JSON-safe conversion for pandas/numpy values returned by the pure core."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd


def to_jsonable(v):
    """Recursively convert numpy/pandas/datetime values into JSON-safe Python."""
    if isinstance(v, dict):
        return {k: to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [to_jsonable(x) for x in v]
    if isinstance(v, np.ndarray):
        return [to_jsonable(x) for x in v.tolist()]

    # scalar from here on
    try:
        if pd.isna(v):  # handles NaN, NaT, None
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, pd.Period):
        return str(v)
    return v


def df_records(df: pd.DataFrame) -> list[dict]:
    """A DataFrame as a list of JSON-safe row dicts."""
    if df is None or df.empty:
        return []
    return [to_jsonable(rec) for rec in df.to_dict(orient="records")]
