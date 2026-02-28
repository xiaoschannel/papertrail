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

receipts = df[df["document_type"] == "receipt"]
dated_receipts = receipts[receipts["parsed_date"].notna()].copy()

# === Monthly Spending Timeline ===
if not dated_receipts.empty:
    st.subheader("Monthly Spending")
    years = sorted(dated_receipts["year"].astype(int).unique())
    view_options = ["All Years", "Overlay by Year"] + [str(y) for y in years]
    view_mode = st.selectbox("View", view_options, key="dash_year_view")

    if view_mode == "Overlay by Year":
        dated_receipts["month_num"] = dated_receipts["parsed_date"].dt.month
        dated_receipts["year_str"] = dated_receipts["year"].astype(int).astype(str)
        monthly = dated_receipts.groupby(["year_str", "month_num"])["cost"].sum().reset_index()
        fig = px.bar(monthly, x="month_num", y="cost", color="year_str")
        month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        fig.update_layout(
            xaxis=dict(tickmode="array", tickvals=list(range(1, 13)), ticktext=month_labels),
            xaxis_title="", yaxis_title="Spend", legend_title="Year",
        )
    elif view_mode in [str(y) for y in years]:
        year_data = dated_receipts[dated_receipts["year"] == int(view_mode)]
        monthly = year_data.groupby(year_data["parsed_date"].dt.to_period("M"))["cost"].sum().rename("spend").reset_index()
        monthly.columns = ["period", "spend"]
        monthly["month"] = monthly["period"].dt.to_timestamp()
        fig = px.bar(monthly, x="month", y="spend")
        fig.update_layout(xaxis_title="", yaxis_title="Spend")
    else:
        monthly = dated_receipts.groupby(dated_receipts["parsed_date"].dt.to_period("M"))["cost"].sum().rename("spend").reset_index()
        monthly.columns = ["period", "spend"]
        monthly["month"] = monthly["period"].dt.to_timestamp()
        fig = px.bar(monthly, x="month", y="spend")
        fig.update_layout(xaxis_title="", yaxis_title="Spend")

    st.plotly_chart(fig, width="stretch")

# === Document Volume ===
st.subheader("Document Volume")
all_dated = df[df["parsed_date"].notna()]
if not all_dated.empty:
    vol = all_dated.groupby(all_dated["parsed_date"].dt.to_period("M")).size().rename("count").reset_index()
    vol.columns = ["period", "count"]
    vol["month"] = vol["period"].dt.to_timestamp()
    fig_vol = px.bar(vol, x="month", y="count")
    fig_vol.update_layout(xaxis_title="", yaxis_title="Documents")
    st.plotly_chart(fig_vol, width="stretch")
else:
    st.info("No dated documents found.")

# === Top Merchants ===
if not receipts.empty:
    st.subheader("Top Merchants")
    rank_by = st.radio("Rank by", ["Total Spend", "Visit Count"], horizontal=True, key="dash_rank")

    leaderboard = receipts.groupby("name").agg(
        total_spend=("cost", "sum"),
        visit_count=("cost", "size"),
    ).reset_index()

    value_col = "total_spend" if rank_by == "Total Spend" else "visit_count"
    leaderboard = leaderboard.sort_values(value_col, ascending=False).head(20)

    fig_lb = px.bar(leaderboard, x=value_col, y="name", orientation="h",
                    labels={value_col: rank_by, "name": ""})
    fig_lb.update_layout(yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig_lb, width="stretch")

    leaderboard["link"] = leaderboard["name"].apply(merchant_url)
    st.dataframe(
        leaderboard[["name", "total_spend", "visit_count", "link"]],
        column_config={
            "name": "Merchant",
            "total_spend": st.column_config.NumberColumn("Total Spend", format="%,.0f"),
            "visit_count": st.column_config.NumberColumn("Visits"),
            "link": st.column_config.LinkColumn("Profile", display_text="View →"),
        },
        hide_index=True,
        width="stretch",
    )
