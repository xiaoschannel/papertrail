import os
from datetime import datetime
from pathlib import Path

import streamlit as st

from archive_audit import batch_statistics, count_duplicate_filenames, disk_vs_index_delta
from models import iter_indexed_files, load_scan_index
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
stats = batch_statistics(scan_index)
duplicates = count_duplicate_filenames(scan_index)
all_entries = iter_indexed_files(scan_index, include_archived=True)
unique_filenames = {fn for _b, _s, fn in all_entries}

st.subheader("Totals")

t1, t2, t3, t4 = st.columns(4)
t1.metric("Total batches", stats["total_batches"])
t2.metric("Archived", stats["archived"])
t3.metric("Non-archived", stats["non_archived"])
t4.metric("Total file entries", stats["total_entries"])

u1, u2, u3 = st.columns(3)
u1.metric("Unique filenames (= 'Indexed' on File Index)", stats["unique_filenames"])
u2.metric("Duplicate filenames across batches", len(duplicates))
u3.metric("Lost to dedup", stats["lost_to_dedup"])

if duplicates:
    st.warning(f"{len(duplicates)} filename(s) appear in multiple batches. "
               f"The 'Indexed' count on File Index only counts unique filenames, "
               f"so {stats['lost_to_dedup']} entries are hidden by dedup.")
    with st.expander("Duplicate filenames"):
        for fn, count in sorted(duplicates.items()):
            batch_ids = [batch_id for batch_id, _, f in all_entries if f == fn]
            st.text(f"{fn} — appears {count}x in batches {batch_ids}")

st.subheader("Per-batch breakdown")
batch_data = [
    {
        "Batch ID": r["batch_id"],
        "Files": r["files"],
        "Running Total": r["running_total"],
        "Archived": r["archived"],
        "Start": r["start"],
        "End": r["end"],
    }
    for r in stats["per_batch"]
]
st.dataframe(batch_data, hide_index=True, width="stretch")

if input_path and input_path.exists():
    st.subheader("Input folder comparison")
    disk_files = {f.name for f in input_path.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS}
    indexed_but_not_on_disk, on_disk_but_not_indexed = disk_vs_index_delta(disk_files, unique_filenames)

    d1, d2, d3 = st.columns(3)
    d1.metric("Image files on disk", len(disk_files))
    d2.metric("Indexed but not on disk (archived/moved)", len(indexed_but_not_on_disk))
    d3.metric("On disk but not indexed", len(on_disk_but_not_indexed))
