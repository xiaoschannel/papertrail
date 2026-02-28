from datetime import date

import streamlit as st

from viz_data import get_output_path, load_viz_records, receipt_url

st.title("Time Capsule")

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
selected_date = st.date_input("On this day...", value=today)

matches = dated[
    (dated["parsed_date"].dt.month == selected_date.month) &
    (dated["parsed_date"].dt.day == selected_date.day)
].sort_values("parsed_date", ascending=False)

if matches.empty:
    st.info(f"No documents found on {selected_date.strftime('%B %d')} in any year.")
    st.stop()

st.markdown(
    f"**{len(matches)}** document(s) on **{selected_date.strftime('%B %d')}** "
    f"across **{int(matches['year'].nunique())}** year(s)."
)

for year_val, group in matches.groupby("year", sort=False):
    st.subheader(str(int(year_val)))
    cols_per_row = 4
    group_rows = list(group.iterrows())
    for i in range(0, len(group_rows), cols_per_row):
        cols = st.columns(cols_per_row)
        for j, (_, row) in enumerate(group_rows[i:i + cols_per_row]):
            with cols[j]:
                if row["path"]:
                    img_path = output_path / row["path"]
                    if img_path.exists():
                        st.image(str(img_path))
                st.markdown(f"**{row['name']}**")
                if row["cost"]:
                    st.caption(f"{row['cost']:,.0f} {row['currency']}")
                st.markdown(f"[View]({receipt_url(row['filename'])})")
