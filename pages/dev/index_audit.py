import os
from collections import Counter
from datetime import datetime
from pathlib import Path

import streamlit as st

from models import ScanIndex, iter_indexed_files, load_scan_index
from settings import IMAGE_EXTENSIONS, get_config

st.title("Index Audit")

cfg = get_config()
batch_dir = cfg.batch_output_path
input_dir = cfg.input_image_path

if not batch_dir:
    st.info("Set batch output path in Config first.")
    st.stop()

output_path = Path(batch_dir)
input_path = Path(input_dir) if input_dir else None
batches_path = output_path / "batches.json"

if not batches_path.exists():
    st.info("No batches.json found.")
    st.stop()

stat = batches_path.stat()
st.subheader("batches.json metadata")
c1, c2, c3 = st.columns(3)
c1.metric("File size", f"{stat.st_size:,} bytes")
c2.metric("Last modified", datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"))
c3.metric("Created", datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S"))

scan_index = load_scan_index(output_path)

st.subheader("Totals")

all_entries = iter_indexed_files(scan_index, include_archived=True)
all_filenames = [fn for _, _, fn in all_entries]
unique_filenames = set(all_filenames)
filename_counts = Counter(all_filenames)
duplicates = {fn: count for fn, count in filename_counts.items() if count > 1}

total_archived = sum(1 for b in scan_index.batches if b.archived)
total_non_archived = sum(1 for b in scan_index.batches if not b.archived)

t1, t2, t3, t4 = st.columns(4)
t1.metric("Total batches", len(scan_index.batches))
t2.metric("Archived", total_archived)
t3.metric("Non-archived", total_non_archived)
t4.metric("Total file entries", len(all_entries))

u1, u2, u3 = st.columns(3)
u1.metric("Unique filenames (= 'Indexed' on File Index)", len(unique_filenames))
u2.metric("Duplicate filenames across batches", len(duplicates))
u3.metric("Lost to dedup", len(all_entries) - len(unique_filenames))

if duplicates:
    st.warning(f"{len(duplicates)} filename(s) appear in multiple batches. "
               f"The 'Indexed' count on File Index only counts unique filenames, "
               f"so {len(all_entries) - len(unique_filenames)} entries are hidden by dedup.")
    with st.expander("Duplicate filenames"):
        for fn, count in sorted(duplicates.items()):
            batch_ids = [batch_id for batch_id, _, f in all_entries if f == fn]
            st.text(f"{fn} — appears {count}x in batches {batch_ids}")

st.subheader("Per-batch breakdown")
batch_data = []
running_total = 0
for batch in scan_index.batches:
    running_total += len(batch.files)
    batch_data.append({
        "Batch ID": batch.batch_id,
        "Files": len(batch.files),
        "Running Total": running_total,
        "Archived": batch.archived,
        "Start": batch.start_datetime,
        "End": batch.end_datetime,
    })
st.dataframe(batch_data, hide_index=True, width="stretch")

if input_path and input_path.exists():
    st.subheader("Input folder comparison")
    disk_files = {f.name for f in input_path.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS}
    indexed_but_not_on_disk = unique_filenames - disk_files
    on_disk_but_not_indexed = disk_files - unique_filenames

    d1, d2, d3 = st.columns(3)
    d1.metric("Image files on disk", len(disk_files))
    d2.metric("Indexed but not on disk (archived/moved)", len(indexed_but_not_on_disk))
    d3.metric("On disk but not indexed", len(on_disk_but_not_indexed))
