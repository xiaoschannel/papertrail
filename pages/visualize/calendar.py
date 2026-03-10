from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

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
period_param = st.query_params.get("period", "week")
date_param = st.query_params.get("date", "")
if period_param not in ("week", "month"):
    period_param = "week"
if "cal_view_date" not in st.session_state:
    try:
        if date_param:
            parsed = date.fromisoformat(date_param)
            anchor = monday_of_week(parsed) if period_param == "week" else first_of_month(parsed)
        else:
            anchor = monday_of_week(today) if period_param == "week" else first_of_month(today)
    except ValueError:
        anchor = monday_of_week(today) if period_param == "week" else first_of_month(today)
    st.session_state["cal_view_period"] = period_param
    st.session_state["cal_view_date"] = anchor.isoformat()
elif date_param:
    try:
        parsed = date.fromisoformat(date_param)
        anchor = monday_of_week(parsed) if period_param == "week" else first_of_month(parsed)
        if st.session_state["cal_view_date"] != anchor.isoformat() or st.session_state["cal_view_period"] != period_param:
            st.session_state["cal_view_period"] = period_param
            st.session_state["cal_view_date"] = anchor.isoformat()
    except ValueError:
        pass

current_date = date.fromisoformat(st.session_state["cal_view_date"])
period_param = st.session_state["cal_view_period"]

period = st.radio("View", ["week", "month"], horizontal=True, index=0 if period_param == "week" else 1, key="cal_period")
if period != period_param:
    st.session_state["cal_view_period"] = period
    st.session_state["cal_view_date"] = (
        monday_of_week(current_date).isoformat()
        if period == "week"
        else first_of_month(current_date).isoformat()
    )
    st.rerun()

go_date = st.date_input(
    "Go to date",
    value=current_date if period == "week" else first_of_month(current_date),
    key="cal_go_date",
)
if period == "week":
    target = monday_of_week(go_date)
else:
    target = first_of_month(go_date)
if target != current_date:
    st.session_state["cal_view_date"] = target.isoformat()
    st.session_state["cal_view_period"] = period
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

prev_date: date
next_date: date
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

nav_cols = st.columns([1, 2, 1])
with nav_cols[0]:
    prev_url = f"/calendar?period={period}&date={prev_date.isoformat()}"
    st.link_button("← Prev", prev_url)
with nav_cols[1]:
    st.markdown(f"**{period_label}**")
with nav_cols[2]:
    next_url = f"/calendar?period={period}&date={next_date.isoformat()}"
    st.link_button("Next →", next_url)

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
                    for row in day_records:
                        render_receipt_card(row, output_path)
                    if not day_records:
                        st.caption("—")
                else:
                    st.caption("")
        week_start += timedelta(days=7)
