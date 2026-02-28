from pathlib import Path

import pandas as pd
import streamlit as st

from viz_data import get_output_path, load_viz_records, merchant_url, sync_query_param

st.title("Receipt Detail")

output_path = get_output_path()
if not output_path:
    st.info("Set batch output path in Config first.")
    st.stop()

df = load_viz_records(str(output_path))
if df.empty:
    st.info("No archived documents found.")
    st.stop()

filenames = sorted(df["filename"].tolist())
sync_query_param("file", "viz_receipt_file", filenames)
selected = st.selectbox("Document", filenames, key="viz_receipt_file")

record = df[df["filename"] == selected].iloc[0]

st.markdown(f"### {record['name'] or record['filename']}")

col_img, col_meta = st.columns([1, 1])

with col_img:
    if record["path"]:
        image_path = output_path / record["path"]
        if image_path.exists():
            st.image(str(image_path), width="stretch")

with col_meta:
    st.markdown(f"**Date:** {record['date']}")
    st.markdown(f"**Time:** {record['time']}")
    if record["document_type"] == "receipt":
        st.markdown(f"**Cost:** {record['cost']:,.2f} {record['currency']}")
    if record["location"]:
        st.markdown(f"**Location:** {record['location']}")
    if record["language"]:
        st.markdown(f"**Language:** {record['language']}")
    st.markdown(f"**Type:** {record['document_type']}")
    st.markdown(f"**Original file:** `{record['filename']}`")
    if record["document_type"] == "receipt" and record["name"]:
        st.markdown(f"[View Merchant Profile →]({merchant_url(record['name'])})")

if record["document_type"] == "receipt" and record["items"]:
    st.subheader("Line Items")
    st.dataframe(pd.DataFrame(record["items"]), hide_index=True, width="stretch")

if record["ocr_markdown"]:
    with st.expander("Raw OCR Text"):
        st.code(record["ocr_markdown"])
