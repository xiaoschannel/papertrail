from pathlib import Path

import streamlit as st

from data import (
    _iter_year_month_dirs,
    build_document_index,
    load_decisions,
    read_sidecar,
    save_decisions,
    write_sidecar,
)
from models import (
    DocumentKey,
    ReviewDecision,
    batch_serial_key,
    iter_indexed_files,
    load_scan_index,
)
from settings import get_config
from validation import is_date_time_safe_for_archive

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

st.title("Fix Invalid Dates")

st.markdown(
    "Detects accepted items with date/time that would break archiving (e.g. invalid format, unparseable). "
    "Allows manual correction."
)

cfg = get_config()
batch_dir = cfg.get("batch_output_path", "")
image_dir = cfg.get("input_image_path", "")

if not batch_dir:
    st.info("Set batch output path in Config first.")
    st.stop()

output_path = Path(batch_dir)
input_path = Path(image_dir) if image_dir else None

key_to_filename: dict[str, str] = {}
index = None
index_file = output_path / "batches.json"
if index_file.exists():
    scan_index = load_scan_index(output_path)
    indexed_keys = {batch_serial_key(bid, ser) for bid, ser, _ in iter_indexed_files(scan_index, include_archived=False)}
    key_to_filename = {batch_serial_key(bid, ser): fn for bid, ser, fn in iter_indexed_files(scan_index, include_archived=False)}
    index = build_document_index(output_path, indexed_keys)

pre_archive_invalid: list[tuple[str, ReviewDecision]] = []
decisions = load_decisions(output_path)
for doc_key, dec in decisions.items():
    if dec.verdict != "accepted":
        continue
    safe, _ = is_date_time_safe_for_archive(dec.date, dec.time)
    if not safe:
        pre_archive_invalid.append((doc_key, dec))

archived_invalid: list[tuple[Path, dict, str]] = []
for month_dir in _iter_year_month_dirs(output_path):
    for sidecar_path in month_dir.glob("*.json"):
        data_file = next(
            (p for p in month_dir.iterdir() if p.stem == sidecar_path.stem and p.suffix.lower() != ".json"),
            None,
        )
        if not data_file:
            continue
        entry = read_sidecar(data_file)
        review = entry.get("review", {})
        if review.get("verdict") != "accepted":
            continue
        date_val = review.get("date", "")
        time_val = review.get("time", "")
        safe, err = is_date_time_safe_for_archive(date_val, time_val)
        if not safe:
            doc_key = entry.get("document_key") or entry.get("original_filename", sidecar_path.stem)
            archived_invalid.append((data_file, entry, err))

total = len(pre_archive_invalid) + len(archived_invalid)
if total == 0:
    st.success("No invalid date/time found.")
    st.stop()

st.warning(f"Found {total} item(s) with invalid date/time: {len(pre_archive_invalid)} pre-archive, {len(archived_invalid)} archived.")

if pre_archive_invalid:
    st.subheader("Pre-archive (decisions.json)")
    for doc_key, dec in pre_archive_invalid:
        safe, err = is_date_time_safe_for_archive(dec.date, dec.time)
        with st.expander(f"{doc_key} — {dec.name or '?'} — {err}"):
            key = f"pre_{doc_key}"
            img_col, form_col = st.columns([1, 2])
            with img_col:
                img_path = None
                if index and input_path:
                    dk = DocumentKey.parse(doc_key) or DocumentKey.from_group(doc_key.split("|") if "|" in doc_key else [doc_key])
                    keys = index.keys_for_doc(dk)
                    fn = key_to_filename.get(keys[0]) if keys else None
                    if fn:
                        img_path = input_path / fn
                if img_path and img_path.exists():
                    st.image(str(img_path), caption=img_path.name, width="stretch")
                else:
                    st.caption("(no image)")
            with form_col:
                new_date = st.text_input("Date", value=dec.date, key=f"{key}_date")
                new_time = st.text_input("Time", value=dec.time, key=f"{key}_time")
                if st.button("Fix", key=f"{key}_btn"):
                    safe_new, err_new = is_date_time_safe_for_archive(new_date, new_time)
                    if not safe_new:
                        st.error(err_new)
                    else:
                        dec_new = ReviewDecision(
                            verdict=dec.verdict,
                            document_type=dec.document_type,
                            name=dec.name,
                            date=new_date,
                            time=new_time,
                            cost=dec.cost,
                            currency=dec.currency,
                        )
                        decisions[doc_key] = dec_new
                        save_decisions(output_path, decisions)
                        st.success("Fixed.")
                        st.rerun()

if archived_invalid:
    st.subheader("Archived (sidecars)")
    doc_key_to_sidecars: dict[str, list[Path]] = {}
    for data_file, entry, err in archived_invalid:
        doc_key = entry.get("document_key") or entry.get("original_filename", data_file.stem)
        doc_key_to_sidecars.setdefault(doc_key, []).append(data_file)

    for doc_key, data_files in doc_key_to_sidecars.items():
        data_file = data_files[0]
        entry = read_sidecar(data_file)
        review = entry.get("review", {})
        safe, err = is_date_time_safe_for_archive(review.get("date", ""), review.get("time", ""))
        with st.expander(f"{doc_key} — {review.get('name', '?')} — {err}"):
            key = f"arch_{doc_key}"
            img_col, form_col = st.columns([1, 2])
            with img_col:
                if data_file.exists() and data_file.suffix.lower() in IMAGE_SUFFIXES:
                    st.image(str(data_file), caption=data_file.name, width="stretch")
                else:
                    st.caption("(no image)")
            with form_col:
                new_date = st.text_input("Date", value=review.get("date", ""), key=f"{key}_date")
                new_time = st.text_input("Time", value=review.get("time", ""), key=f"{key}_time")
                if st.button("Fix", key=f"{key}_btn"):
                    safe_new, err_new = is_date_time_safe_for_archive(new_date, new_time)
                    if not safe_new:
                        st.error(err_new)
                    else:
                        review["date"] = new_date
                        review["time"] = new_time
                        entry["review"] = review
                        for df in data_files:
                            ent = read_sidecar(df)
                            ent["review"] = review
                            write_sidecar(df, ent)
                        st.success(f"Fixed {len(data_files)} sidecar(s).")
                        st.rerun()
