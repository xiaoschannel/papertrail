from datetime import datetime
from pathlib import Path

import streamlit as st

from indexing_schemes import SCHEMES, parse_canon_filename
from models import ScanBatch, ScanIndex, load_scan_index
from settings import IMAGE_EXTENSIONS, get_config

st.title("File Index")

cfg = get_config()
input_dir = cfg.get("input_image_path", "")
output_dir = cfg.get("batch_output_path", "")

if not input_dir or not output_dir:
    st.info("Set input image path and batch output path in Config first.")
    st.stop()

input_path = Path(input_dir)
output_path = Path(output_dir)

image_files = [f for f in input_path.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS]
all_filenames = [f.name for f in image_files]

existing_index: ScanIndex | None = None
indexed_filenames: set[str] = set()
index_file = output_path / "batches.json"
if index_file.exists():
    existing_index, filename_to_batch = load_scan_index(output_path)
    indexed_filenames = set(filename_to_batch.keys())

unindexed_filenames = [fn for fn in all_filenames if fn not in indexed_filenames]

st.subheader("Summary")
col0, col1, col2 = st.columns(3)
col0.metric("Image files", len(image_files))
col1.metric("Indexed", len(indexed_filenames))
col2.metric("Unindexed", len(unindexed_filenames))

if existing_index:
    st.markdown(f"**Existing index:** {len(existing_index.batches)} batch(es)")

if not unindexed_filenames:
    st.success("All files are already indexed.")
    st.stop()

scheme_name = st.selectbox(
    "Indexing scheme",
    options=list(SCHEMES.keys()),
    help="Only unindexed files are passed to the scheme. Already-indexed files are never reassigned.",
)
scheme = SCHEMES[scheme_name]
new_batches_rel, scheme_skipped, warnings = scheme(unindexed_filenames)

has_error = False
if scheme_name == "Canon ImageFormula" and existing_index and existing_index.batches and new_batches_rel:
    last_batch = existing_index.batches[-1]
    last_end = datetime.strptime(last_batch.end_datetime, "%Y-%m-%d %H:%M:%S")
    unindexed_parsed = [(parse_canon_filename(fn), fn) for fn in unindexed_filenames if fn not in scheme_skipped]
    before_last = [fn for (p, fn) in unindexed_parsed if p and p[0] < last_end]
    if before_last:
        has_error = True
        st.error(
            f"{len(before_last)} unindexed file(s) have timestamps before the last batch's end "
            f"({last_batch.end_datetime}). This indicates files were missed. "
            f"Delete batches.json and rebuild from scratch to fix."
        )
        with st.expander("Offending files"):
            for fn in before_last:
                st.text(f"  {fn}")

if scheme_skipped:
    with st.expander(f"{len(scheme_skipped)} skipped by scheme (not assigned)"):
        for name in scheme_skipped:
            st.text(name)

if warnings:
    for w in warnings:
        st.warning(w)

if has_error:
    st.stop()

start_batch_id = existing_index.batches[-1].batch_id + 1 if existing_index and existing_index.batches else 1
new_batches: list[ScanBatch] = []
for i, b in enumerate(new_batches_rel):
    new_batches.append(ScanBatch(
        batch_id=start_batch_id + i,
        start_datetime=b.start_datetime,
        end_datetime=b.end_datetime,
        files=b.files,
    ))
final_batches = (existing_index.batches + new_batches) if existing_index else new_batches

if new_batches:
    st.subheader(f"{len(new_batches)} new batch(es)")
    for b in new_batches:
        with st.expander(f"Batch {b.batch_id} — {len(b.files)} files — {b.start_datetime} to {b.end_datetime}"):
            for serial in sorted(b.files):
                st.text(f"  {serial:03d}  {b.files[serial]}")

if st.button("Build Index", width="stretch", type="primary"):
    output_path.mkdir(parents=True, exist_ok=True)
    index = ScanIndex(batches=final_batches)
    (output_path / "batches.json").write_text(index.model_dump_json(indent=2), encoding="utf-8")
    newly_indexed = sum(len(b.files) for b in new_batches)
    st.success(f"Saved batches.json with {len(final_batches)} batches ({newly_indexed} new files)")
    st.rerun()
