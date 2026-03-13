
import pandas as pd
import streamlit as st

from data import (
    load_name_cache,
    read_sidecar,
    save_name_cache,
    write_sidecar,
)
from models import ReviewDecision, batch_serial_key
from organize_utils import move_to_accepted_destination
from viz_data import (
    get_output_path,
    load_viz_records,
    merchant_url,
    sync_query_param,
)

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

if st.session_state.get("receipt_edit_file") and st.session_state.receipt_edit_file != selected:
    st.session_state.receipt_edit_file = None

data_file = output_path / record["path"] if record["path"] else None
entry = read_sidecar(data_file) if data_file else {}
can_edit = bool(record["path"] and entry)

edit_mode = can_edit and st.session_state.get("receipt_edit_file") == selected

title_col, btn_col = st.columns([6, 1])
with title_col:
    st.markdown(f"### {record['name'] or record['filename']}")
with btn_col:
    if edit_mode:
        btn_save = st.button("Save", icon=":material/check:", key="receipt_save")
        btn_cancel = st.button("Cancel", icon=":material/close:", key="receipt_cancel")
        if btn_cancel:
            st.session_state.receipt_edit_file = None
            st.rerun()
    elif can_edit:
        if st.button("Edit", icon=":material/edit:", key="receipt_edit"):
            st.session_state.receipt_edit_file = selected
            st.rerun()

col_img, col_meta = st.columns([1, 1])

with col_img:
    paths = record.get("paths", [record["path"]] if record["path"] else [])
    for i, p in enumerate(paths):
        if p:
            image_path = output_path / p
            if image_path.exists():
                st.image(str(image_path), caption=f"Page {i + 1}" if len(paths) > 1 else None, width="stretch")

with col_meta:
    if edit_mode:
        review = entry.get("review", {})
        ext = entry.get("extraction") or {}
        default_doc_type = review.get("document_type", "receipt")
        default_name = review.get("name", record["name"] or "")
        default_date = review.get("date", record["date"] or "")
        default_time = review.get("time", record["time"] or "")
        default_cost = float(review.get("cost", record.get("cost", 0)))
        default_currency = review.get("currency", record.get("currency", ""))
        default_location = ext.get("location", record.get("location", ""))
        default_language = ext.get("language", record.get("language", ""))

        doc_type_options = ["receipt", "other", "corrupted"]
        doc_type = st.radio(
            "Type",
            doc_type_options,
            index=doc_type_options.index(default_doc_type) if default_doc_type in doc_type_options else 0,
            horizontal=True,
            key="receipt_doc_type",
        )
        name = st.text_input("Name", value=default_name, key="receipt_name")
        dt_cols = st.columns(2)
        date_val = dt_cols[0].text_input("Date", value=default_date, key="receipt_date")
        time_val = dt_cols[1].text_input("Time", value=default_time, key="receipt_time")

        if doc_type == "receipt":
            cost_cols = st.columns([2, 1, 1])
            cost_display = str(int(default_cost)) if default_cost == int(default_cost) else str(default_cost)
            cost_str = cost_cols[0].text_input("Cost", value=cost_display, key="receipt_cost")
            currency_val = cost_cols[1].text_input("Currency", value=default_currency, key="receipt_currency")
            jpy_checked = cost_cols[2].checkbox("JPY", value=(default_currency.upper() == "JPY"), key="receipt_jpy")
            st.markdown(
                "<style>[data-testid='stHorizontalBlock'] [data-testid='stCheckbox']{padding-top:2.1rem;}</style>",
                unsafe_allow_html=True,
            )
            location_val = st.text_input("Location", value=default_location, key="receipt_location")
            language_val = st.text_input("Language", value=default_language, key="receipt_language")
        else:
            cost_str = "0"
            currency_val = ""
            jpy_checked = False
            location_val = default_location
            language_val = default_language

        if btn_save:
            try:
                parsed_cost = float(cost_str)
            except ValueError:
                parsed_cost = 0.0
            final_currency = "JPY" if jpy_checked else currency_val
            receipt_ok = doc_type != "receipt" or bool(cost_str.strip() and final_currency)
            if not receipt_ok:
                missing = []
                if doc_type == "receipt":
                    if not cost_str.strip():
                        missing.append("cost")
                    if not final_currency:
                        missing.append("currency")
                if missing:
                    st.error(f"Receipt requires: {', '.join(missing)}")
            if receipt_ok:
                dec = ReviewDecision(
                    verdict=entry.get("review", {}).get("verdict", "accepted"),
                    document_type=doc_type,
                    name=name,
                    date=date_val,
                    time=time_val,
                    cost=parsed_cost,
                    currency=final_currency,
                )
                orig_fn = entry.get("original_filename", selected)
                target_path = move_to_accepted_destination(output_path, orig_fn, data_file, dec)
                entry["review"] = dec.model_dump()
                if doc_type == "receipt" and entry.get("extraction"):
                    ext = entry["extraction"]
                    ext["location"] = location_val
                    ext["language"] = language_val
                    ext["name"] = name
                    ext["date"] = date_val
                    ext["time"] = time_val
                    ext["cost"] = parsed_cost
                    ext["currency"] = final_currency
                write_sidecar(target_path, entry)
                name_cache = load_name_cache(output_path)
                ext_name = (entry.get("extraction") or {}).get("name", "")
                cache_key = entry.get("document_key") or (batch_serial_key(bid, ser) if (bid := entry.get("batch_id")) is not None and (ser := entry.get("serial")) is not None else selected)
                name_cache[cache_key] = {"extracted": ext_name, "confirmed": name}
                save_name_cache(output_path, name_cache)
                load_viz_records.clear()
                st.session_state.receipt_edit_file = None
                st.rerun()
    else:
        st.markdown(f"**Date:** {record['date']}")
        st.markdown(f"**Time:** {record['time']}")
        if record["document_type"] == "receipt":
            st.markdown(f"**Cost:** {record['cost']:,.2f} {record['currency']}")
        if record["location"]:
            st.markdown(f"**Location:** {record['location']}")
        if record["language"]:
            st.markdown(f"**Language:** {record['language']}")
        st.markdown(f"**Type:** {record['document_type']}")
        n_pages = len(record.get("paths", [record["path"]] if record["path"] else []))
        if n_pages > 1:
            st.markdown(f"**Document:** `{record['filename']}` ({n_pages} pages)")
        else:
            st.markdown(f"**Original file:** `{record['filename']}`")
        if record["document_type"] == "receipt" and record["name"]:
            st.markdown(f"[View Merchant Profile →]({merchant_url(record['name'])})")

if record["document_type"] == "receipt" and record["items"]:
    st.subheader("Line Items")
    st.dataframe(pd.DataFrame(record["items"]), hide_index=True, width="stretch")

if record["ocr_markdown"]:
    with st.expander("Raw OCR Text"):
        st.code(record["ocr_markdown"])
