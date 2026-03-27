from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
from PIL import Image

from settings import get_config, update_config
from viz_data import get_output_path, load_viz_records, receipt_url

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def first_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def first_of_next_month(d: date) -> date:
    y, m = d.year, d.month
    if m == 12:
        return date(y + 1, 1, 1)
    return date(y, m + 1, 1)


def records_in_range(
    dated: pd.DataFrame, range_start: date, range_end: date
) -> dict[date, list]:
    mask = (dated["parsed_date"].dt.date >= range_start) & (
        dated["parsed_date"].dt.date < range_end
    )
    subset = dated.loc[mask]
    out: dict[date, list] = {}
    for val in subset["parsed_date"].dt.date.unique():
        d_py = val if isinstance(val, date) else date(val.year, val.month, val.day)
        out[d_py] = subset[subset["parsed_date"].dt.date == val].to_dict("records")
    return out


@st.cache_data
def _image_aspect(path_str: str) -> float:
    img = Image.open(path_str)
    w, h = img.size
    img.close()
    return h / max(w, 1)


def estimate_card_height(row: dict, output_path: Path) -> float:
    base = 3.0
    if row.get("path"):
        img_path = output_path / row["path"]
        if img_path.exists():
            base += _image_aspect(str(img_path)) * 10
    return base


def assign_to_columns(records: list[dict], output_path: Path) -> tuple[list, list]:
    col1, col2 = [], []
    h1, h2 = 0.0, 0.0
    for row in records:
        card_h = estimate_card_height(row, output_path)
        if h1 <= h2:
            col1.append(row)
            h1 += card_h
        else:
            col2.append(row)
            h2 += card_h
    return col1, col2


def render_receipt_card(row: dict, output_path: Path) -> None:
    if row.get("path"):
        img_path = output_path / row["path"]
        if img_path.exists():
            st.image(str(img_path))
    st.markdown(f"**{row['name']}**")
    st.caption(f"{row['cost']:,.0f} {row['currency']}")
    st.markdown(f"[View]({receipt_url(row['filename'])})")


st.title("Calendar")

output_path = get_output_path()
if not output_path:
    st.info("Set batch output path in Config first.")
    st.stop()

df = load_viz_records(str(output_path))
if df.empty:
    st.info("No archived documents found.")
    st.stop()

dated = df[df["parsed_date"].notna()].copy()
if dated.empty:
    st.info("No dated documents found.")
    st.stop()

today = date.today()
cfg = get_config()

if "cal_view_date" not in st.session_state:
    saved_period = cfg.calendar_period if cfg.calendar_period in ("week", "month") else "week"
    if cfg.calendar_date:
        parsed = date.fromisoformat(cfg.calendar_date)
        anchor = monday_of_week(parsed) if saved_period == "week" else first_of_month(parsed)
    else:
        anchor = monday_of_week(today) if saved_period == "week" else first_of_month(today)
    st.session_state["cal_view_period"] = saved_period
    st.session_state["cal_view_date"] = anchor.isoformat()

data_min = date(dated["parsed_date"].min().year, dated["parsed_date"].min().month, dated["parsed_date"].min().day)
data_max = date(dated["parsed_date"].max().year, dated["parsed_date"].max().month, dated["parsed_date"].max().day)

current_date = min(max(date.fromisoformat(st.session_state["cal_view_date"]), data_min), data_max)
if st.session_state["cal_view_date"] != current_date.isoformat():
    st.session_state["cal_view_date"] = current_date.isoformat()
period_param = st.session_state["cal_view_period"]

period = st.radio("View", ["week", "month"], horizontal=True, index=0 if period_param == "week" else 1, key="cal_period")
if period != period_param:
    new_anchor = monday_of_week(current_date) if period == "week" else first_of_month(current_date)
    st.session_state["cal_view_period"] = period
    st.session_state["cal_view_date"] = new_anchor.isoformat()
    st.session_state["_cal_pending_go_date"] = min(max(new_anchor, data_min), data_max)
    update_config(calendar_period=period, calendar_date=new_anchor.isoformat())
    st.rerun()

if "_cal_pending_go_date" in st.session_state:
    st.session_state["cal_go_date"] = st.session_state.pop("_cal_pending_go_date")

_default = current_date if period == "week" else first_of_month(current_date)
go_date = st.date_input(
    "Go to date",
    value=min(max(_default, data_min), data_max),
    min_value=data_min,
    max_value=data_max,
    key="cal_go_date",
)
target = monday_of_week(go_date) if period == "week" else first_of_month(go_date)
if target != current_date:
    st.session_state["cal_view_date"] = target.isoformat()
    st.session_state["cal_view_period"] = period
    update_config(calendar_period=period, calendar_date=target.isoformat())
    st.rerun()

if period == "week":
    range_start = current_date
    range_end = current_date + timedelta(days=7)
    period_label = f"Week of {current_date.strftime('%b %d, %Y')}"
else:
    range_start = current_date
    range_end = first_of_next_month(current_date)
    period_label = current_date.strftime("%B %Y")

by_date = records_in_range(dated, range_start, range_end)

if period == "week":
    prev_date = current_date - timedelta(days=7)
    next_date = current_date + timedelta(days=7)
else:
    prev_date = (
        date(current_date.year - 1, 12, 1)
        if current_date.month == 1
        else date(current_date.year, current_date.month - 1, 1)
    )
    next_date = first_of_next_month(current_date)

nav_cols = st.columns([3, 4, 3])
with nav_cols[0]:
    if st.button("← Prev", width="stretch"):
        st.session_state["cal_view_date"] = prev_date.isoformat()
        st.session_state["cal_view_period"] = period
        st.session_state["_cal_pending_go_date"] = min(max(prev_date, data_min), data_max)
        update_config(calendar_period=period, calendar_date=prev_date.isoformat())
        st.rerun()
with nav_cols[1]:
    st.markdown(f"**{period_label}**", text_alignment="center")
with nav_cols[2]:
    if st.button("Next →", width="stretch"):
        st.session_state["cal_view_date"] = next_date.isoformat()
        st.session_state["cal_view_period"] = period
        st.session_state["_cal_pending_go_date"] = min(max(next_date, data_min), data_max)
        update_config(calendar_period=period, calendar_date=next_date.isoformat())
        st.rerun()

st.divider()

if period == "week":
    cols = st.columns(7)
    for i in range(7):
        d = current_date + timedelta(days=i)
        with cols[i]:
            st.markdown(f"**{WEEKDAYS[i]} {d.day}**")
            day_records = by_date.get(d, [])
            for row in day_records:
                render_receipt_card(row, output_path)
            if not day_records:
                st.caption("—")
else:
    header_cols = st.columns(7)
    for i, wd in enumerate(WEEKDAYS):
        header_cols[i].markdown(f"**{wd}**")
    week_start = range_start - timedelta(days=range_start.weekday())
    while week_start < range_end:
        row_cols = st.columns(7)
        for i in range(7):
            cell_date = week_start + timedelta(days=i)
            with row_cols[i]:
                if range_start <= cell_date < range_end:
                    st.markdown(f"**{cell_date.day}**")
                    day_records = by_date.get(cell_date, [])
                    if len(day_records) >= 2:
                        col1_items, col2_items = assign_to_columns(day_records, output_path)
                        sub_cols = st.columns(2)
                        with sub_cols[0]:
                            for row in col1_items:
                                render_receipt_card(row, output_path)
                        with sub_cols[1]:
                            for row in col2_items:
                                render_receipt_card(row, output_path)
                    elif day_records:
                        render_receipt_card(day_records[0], output_path)
                    else:
                        st.caption("—")
                else:
                    st.caption("")
        week_start += timedelta(days=7)
