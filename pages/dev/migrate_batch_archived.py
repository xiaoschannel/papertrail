import shutil
from pathlib import Path

import streamlit as st

from data import scan_organized_filenames
from models import load_scan_index
from settings import get_config

st.title("Migrate Batch Archived Status")

st.markdown(
    "One-time migration to add `archived` status to batches. "
    "Batches whose files are all in organized (year/month, marked, tossed) get `archived=True`. "
    "Backs up batches.json before overwriting."
)

cfg = get_config()
batch_dir = cfg.get("batch_output_path", "")

if not batch_dir:
    st.info("Set batch output path in Config first.")
    st.stop()

output_path = Path(batch_dir)
batches_file = output_path / "batches.json"
if not batches_file.exists():
    st.info("Run File Index first to create batches.json.")
    st.stop()

scan_index = load_scan_index(output_path)
organized = scan_organized_filenames(output_path)

to_mark = []
already_archived = []
for batch in scan_index.batches:
    if batch.archived:
        already_archived.append(batch)
    elif all(fn in organized for fn in batch.files.values()):
        to_mark.append(batch)

st.metric("Batches to mark archived", len(to_mark))
st.metric("Already archived", len(already_archived))
st.metric("Pending (not all files organized)", len(scan_index.batches) - len(to_mark) - len(already_archived))

if to_mark:
    with st.expander("Batches to mark archived"):
        for b in to_mark:
            st.text(f"Batch {b.batch_id} — {len(b.files)} files")

if not to_mark:
    st.success("No batches to migrate.")
    st.stop()

if st.button("Run Migration", width="stretch", type="primary"):
    for batch in to_mark:
        batch.archived = True
    shutil.copy(batches_file, batches_file.with_suffix(".json.bak"))
    (output_path / "batches.json").write_text(scan_index.model_dump_json(indent=2), encoding="utf-8")
    st.success(f"Marked {len(to_mark)} batch(es) as archived. Backup saved as batches.json.bak")
    st.rerun()
