import plotly.express as px
import streamlit as st

from viz_data import get_output_path, load_viz_records, merchant_url

st.title("Spending Dashboard")

output_path = get_output_path()
if not output_path:
    st.info("Set batch output path in Config first.")
    st.stop()

df = load_viz_records(str(output_path))
if df.empty:
    st.info("No archived documents found.")
    st.stop()

all_dated = df[df["parsed_date"].notna()].copy()
years = sorted(all_dated["year"].astype(int).dropna().unique()) if not all_dated.empty else []
view_options = ["All Years"] + [str(int(y)) for y in years]
view_mode = st.selectbox("View", view_options, key="dash_year_view")

if view_mode != "All Years":
    year_val = int(view_mode)
    df = df[df["year"] == year_val].copy()
    all_dated = all_dated[all_dated["year"] == year_val].copy()

all_dated["period"] = all_dated["parsed_date"].dt.to_period("M")
all_dated["month_ts"] = all_dated["period"].dt.to_timestamp()

receipts = df[df["document_type"] == "receipt"]
dated_receipts = receipts[receipts["parsed_date"].notna()].copy()
if not dated_receipts.empty:
    dated_receipts["period"] = dated_receipts["parsed_date"].dt.to_period("M")
    dated_receipts["month_ts"] = dated_receipts["period"].dt.to_timestamp()

col1, col2 = st.columns(2)
# === Monthly Spending Timeline ===
with col1:
    if not dated_receipts.empty:
        st.subheader("Monthly Spending")
        monthly = dated_receipts.groupby("period")["cost"].sum().rename("spend").reset_index()
        monthly["month_ts"] = monthly["period"].dt.to_timestamp()
        fig = px.bar(monthly, x="month_ts", y="spend")
        fig.update_layout(xaxis_title="", yaxis_title="Spend")
        st.plotly_chart(fig, width="stretch")

# === Document Volume ===
with col2:
    st.subheader("Document Volume")
    if not all_dated.empty:
        vol = all_dated.groupby("period").size().rename("count").reset_index()
        vol["month_ts"] = vol["period"].dt.to_timestamp()
        fig_vol = px.bar(vol, x="month_ts", y="count")
        fig_vol.update_layout(xaxis_title="", yaxis_title="Documents")
        st.plotly_chart(fig_vol, width="stretch")
    else:
        st.info("No dated documents in this view.")

# === Top Merchants ===
if not receipts.empty:
    st.subheader("Top Merchants")
    rank_by = st.radio("Rank by", ["Total Spend", "Visit Count"], horizontal=True, key="dash_rank")

    col1, col2 = st.columns(2)
    with col1:
        leaderboard = receipts.groupby("name").agg(
            total_spend=("cost", "sum"),
            visit_count=("cost", "size"),
        ).reset_index()
        leaderboard["avg_per_visit"] = (leaderboard["total_spend"] / leaderboard["visit_count"]).round().astype(int)

        value_col = "total_spend" if rank_by == "Total Spend" else "visit_count"
        leaderboard = leaderboard.sort_values(value_col, ascending=False).head(20)

        fig_lb = px.bar(leaderboard, x=value_col, y="name", orientation="h",
                        labels={value_col: rank_by, "name": ""})
        fig_lb.update_layout(yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig_lb, width="stretch")

    with col2:
        leaderboard["link"] = leaderboard["name"].apply(merchant_url)
        st.dataframe(
            leaderboard[["name", "total_spend", "visit_count", "avg_per_visit", "link"]],
            column_config={
                "name": "Merchant",
                "total_spend": st.column_config.NumberColumn("Total Spend"),
                "visit_count": st.column_config.NumberColumn("Visits"),
                "avg_per_visit": st.column_config.NumberColumn("Avg per Visit"),
                "link": st.column_config.LinkColumn("Profile", display_text="View →"),
            },
            hide_index=True,
            width="stretch",
        )
