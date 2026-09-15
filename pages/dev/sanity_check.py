from pathlib import Path

import streamlit as st

from archive_audit import batch_coverage, check_archive_sidecars
from data import scan_organized_filenames
from models import load_scan_index
from settings import get_config

st.title("Sanity Check")

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
    st.info("No batches.json found. Run File Index first.")
    st.stop()

organized = scan_organized_filenames(output_path)
scan_index = load_scan_index(output_path)
coverage = {row["batch_id"]: row for row in batch_coverage(scan_index, organized)}

st.header("Batch Sanity")
for batch in scan_index.batches:
    files_in_batch = set(batch.files.values())
    cov = coverage[batch.batch_id]

    st.subheader(f"Batch {batch.batch_id}")
    col0, col1, col2 = st.columns(3)
    col0.metric("Files in batch", cov["in_batch"])
    col1.metric("Files organized", cov["organized"])
    if not batch.archived:
        if input_path and input_path.exists():
            files_in_input = {fn for fn in files_in_batch if (input_path / fn).is_file()}
            missing_from_input = files_in_batch - files_in_input
            if missing_from_input:
                col2.error(f"Missing from input: {', '.join(sorted(missing_from_input))}")
            else:
                col2.success("All files in input folder")
        else:
            col2.info("Pending archive")
    elif cov["missing"]:
        col2.error(f"Files missing: {', '.join(cov['missing'])}")
    else:
        col2.success("All files archived")

st.header("Archive Sanity")
failures = check_archive_sidecars(output_path)
if not failures:
    st.success("Sanity checks passed for batch coverage and archive metadata.")
else:
    st.error(f"{len(failures)} issue(s) found.")
    for folder, missing_sidecar, extra_sidecar in sorted(failures):
        with st.expander(f"{folder} — data files vs sidecar mismatch"):
            st.markdown(f"missing_sidecar={missing_sidecar}, extra_sidecar={extra_sidecar}")
