
import pandas as pd
import plotly.express as px
import streamlit as st

from viz_data import (
    get_output_path,
    load_viz_items,
    load_viz_records,
    receipt_url,
    sync_query_param,
)

st.title("Merchant Profile")

output_path = get_output_path()
if not output_path:
    st.info("Set batch output path in Config first.")
    st.stop()

df = load_viz_records(str(output_path))
if df.empty:
    st.info("No archived documents found.")
    st.stop()

receipts = df[df["document_type"] == "receipt"]
merchant_names = receipts["name"].value_counts().index.tolist()
if not merchant_names:
    st.info("No receipt data found.")
    st.stop()

match_mode = st.radio("Match by", ["Exact name", "Prefix"], horizontal=True, key="viz_merchant_match")
if match_mode == "Exact name":
    sync_query_param("name", "viz_merchant_name", merchant_names)
    selected = st.selectbox("Merchant", merchant_names, key="viz_merchant_name")
    merchant_df = receipts[receipts["name"] == selected].copy()
else:
    prefix = st.text_input("Merchant name prefix", key="viz_merchant_prefix", placeholder="e.g. Star")
    prefix_clean = (prefix or "").strip()
    if not prefix_clean:
        merchant_df = pd.DataFrame()
        st.info("Enter a prefix to match merchant names.")
    else:
        mask = receipts["name"].str.lower().str.startswith(prefix_clean.lower())
        merchant_df = receipts[mask].copy()
        selected = prefix_clean
        if merchant_df.empty:
            st.warning(f"No receipts match prefix \"{prefix_clean}\".")
        else:
            matched_names = merchant_df["name"].unique().tolist()
            st.caption(f"Matches {len(matched_names)} merchant(s): {', '.join(matched_names[:5])}{'…' if len(matched_names) > 5 else ''}")

if merchant_df.empty:
    st.stop()
dated = merchant_df[merchant_df["parsed_date"].notna()].sort_values("parsed_date")

# --- Header Stats ---
total_spend = merchant_df["cost"].sum()
visit_count = len(merchant_df)
avg_ticket = total_spend / visit_count if visit_count else 0

currency_mode = merchant_df["currency"].mode()
currency = currency_mode.iloc[0] if not currency_mode.empty else ""

first_visit = dated["parsed_date"].min() if not dated.empty else None
last_visit = dated["parsed_date"].max() if not dated.empty else None

avg_gap = None
if len(dated) >= 2:
    avg_gap = dated["parsed_date"].diff().dt.days.dropna().mean()

c1, c2, c3 = st.columns(3)
c1.metric("Total Spend", f"{total_spend:,.0f} {currency}")
c2.metric("Visits", visit_count)
c3.metric("Avg Ticket", f"{avg_ticket:,.0f} {currency}")

c4, c5, c6 = st.columns(3)
c4.metric("First Visit", str(first_visit.date()) if pd.notna(first_visit) else "—")
c5.metric("Last Visit", str(last_visit.date()) if pd.notna(last_visit) else "—")
c6.metric("Avg Days Between Visits", f"{avg_gap:.0f}" if avg_gap is not None else "—")

c1, c2 = st.columns(2)
with c1:
    if not dated.empty:
        st.subheader("Spending Trend")
        monthly = dated.groupby(dated["parsed_date"].dt.to_period("M"))["cost"].sum().rename("spend").reset_index()
        monthly.columns = ["period", "spend"]
        monthly["month"] = monthly["period"].dt.to_timestamp()
        fig = px.bar(monthly, x="month", y="spend")
        fig.update_layout(xaxis_title="", yaxis_title=f"Spend ({currency})")
        st.plotly_chart(fig, width="stretch")

with c2:
    if len(dated) >= 3:
        st.subheader("Visit Cadence")
        sorted_dates = dated["parsed_date"].reset_index(drop=True)
        gap_df = pd.DataFrame({
            "visit_date": sorted_dates.iloc[1:].values,
            "days_since_last": sorted_dates.diff().dt.days.iloc[1:].values,
        })
        fig2 = px.scatter(gap_df, x="visit_date", y="days_since_last")
        fig2.update_layout(xaxis_title="", yaxis_title="Days Since Last Visit")
        st.plotly_chart(fig2, width="stretch")

# --- Item Breakdown ---
items_df = load_viz_items(str(output_path))
matched_names_set = set(merchant_df["name"].unique())
merchant_items = items_df[items_df["merchant"].isin(matched_names_set)] if not items_df.empty else pd.DataFrame()

if not merchant_items.empty:
    st.subheader("Item Breakdown")
    agg = merchant_items.groupby("item_name").agg(
        times_purchased=("item_name", "size"),
        total_spent=("total_price", "sum"),
        avg_unit_price=("unit_price", "mean"),
    ).reset_index()
    st.dataframe(
        agg.sort_values("times_purchased", ascending=False),
        column_config={
            "item_name": "Item",
            "times_purchased": "Count",
            "total_spent": st.column_config.NumberColumn("Total Spent", format="%.0f"),
            "avg_unit_price": st.column_config.NumberColumn("Avg Unit Price", format="%.1f"),
        },
        hide_index=True,
        width="stretch",
    )

# --- Receipt Gallery ---
st.subheader("Receipts")
sorted_receipts = merchant_df.sort_values("parsed_date", ascending=False)
per_page = 20
total_pages = max(1, (len(sorted_receipts) + per_page - 1) // per_page)
page = min(max(0, st.session_state.get("viz_gallery_page", 0)), total_pages - 1)
st.session_state["viz_gallery_page"] = page
start = page * per_page
gallery_df = sorted_receipts.iloc[start : start + per_page]
end = start + len(gallery_df)

def pagination_ui(suffix: str):
    col_prev, col_info, col_next = st.columns([1, 3, 1])
    with col_prev:
        if st.button("← Prev", key=f"gallery_prev_{suffix}", disabled=(page == 0), width="stretch"):
            st.session_state["viz_gallery_page"] = page - 1
            st.rerun()
    with col_info:
        st.caption(f"Page {page + 1} of {total_pages} — {start + 1}–{end} of {len(sorted_receipts)}", text_alignment="center")
    with col_next:
        if st.button("Next →", key=f"gallery_next_{suffix}", disabled=(page >= total_pages - 1), width="stretch"):
            st.session_state["viz_gallery_page"] = page + 1
            st.rerun()

pagination_ui("top")
cols_per_row = 5
gallery_rows = list(gallery_df.iterrows())
for i in range(0, len(gallery_rows), cols_per_row):
    cols = st.columns(cols_per_row)
    for j, (_, row) in enumerate(gallery_rows[i:i + cols_per_row]):
        with cols[j]:
            if row["path"]:
                img_path = output_path / row["path"]
                if img_path.exists():
                    st.image(str(img_path))
            st.caption(f"{row['date']} — {row['cost']:,.0f} {currency}")
            st.markdown(f"[Detail]({receipt_url(row['filename'])})")
pagination_ui("bottom")
