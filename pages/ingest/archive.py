import shutil
from pathlib import Path

import streamlit as st

from data import (
    load_decisions,
    load_extractions,
    load_name_cache,
    load_ocr_results,
    save_name_cache,
    scan_organized_filenames,
    write_sidecar,
)
from models import OcrResult, OtherResult, ReceiptResult, load_scan_index
from organize_utils import plan_accepted_destinations, scan_existing_names
from settings import get_config

st.title("Archive")

cfg = get_config()
batch_dir = cfg.get("batch_output_path", "")
image_dir = cfg.get("input_image_path", "")

if not batch_dir or not image_dir:
    st.info("Set input image path and batch output path in Config first.")
    st.stop()

output_path = Path(batch_dir)
input_path = Path(image_dir)

batch = load_ocr_results(output_path)
ocr_by_file: dict[str, OcrResult] = {r.filename: r for r in batch.results if r.succeeded}

extractions = load_extractions(output_path)

all_decisions = load_decisions(output_path)

filename_to_batch: dict[str, int] = {}
filename_to_serial: dict[str, int] = {}
batches_file = output_path / "batches.json"
if batches_file.exists():
    scan_index, filename_to_batch = load_scan_index(output_path)
    for b in scan_index.batches:
        for serial, fn in b.files.items():
            filename_to_serial[fn] = serial

organized = scan_organized_filenames(output_path)

already_organized = {fn for fn in all_decisions if fn in organized}
decisions = {fn: dec for fn, dec in all_decisions.items() if fn not in organized}

st.metric("Already organized", len(already_organized))
st.metric("New to organize", len(decisions))

if not decisions:
    st.info("No new files to organize.")
    st.stop()

existing_names_by_folder = scan_existing_names(output_path)

accepted_decisions = {fn: dec for fn, dec in decisions.items() if dec.verdict == "accepted"}
marked = [fn for fn, dec in decisions.items() if dec.verdict == "marked"]
tossed = [fn for fn, dec in decisions.items() if dec.verdict not in ("accepted", "marked")]

file_destinations = plan_accepted_destinations(
    accepted_decisions, existing_names_by_folder, filename_to_batch, filename_to_serial,
)
for fn in marked:
    file_destinations[fn] = f"marked/{fn}"
for fn in tossed:
    file_destinations[fn] = f"tossed/{fn}"

n_accepted = len(accepted_decisions)
n_marked = len(marked)
n_tossed = len(tossed)

col0, col1, col2 = st.columns(3)
col0.metric("Accepted", n_accepted)
col1.metric("Marked", n_marked)
col2.metric("Tossed", n_tossed)

st.subheader("Preview")
preview_data = [{"Original": fn, "Destination": dest} for fn, dest in sorted(file_destinations.items())]
st.dataframe(preview_data, hide_index=True, width="stretch")

CLEANUP_ARTIFACTS = ["ocr.json", "extractions.json", "decisions.json"]

if st.button("Archive", width="stretch", type="primary"):
    for fn, dest in file_destinations.items():
        src = input_path / fn
        dst = output_path / dest
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(src), str(dst))

        dec = decisions[fn]
        entry: dict = {
            "original_filename": fn,
            "batch_id": filename_to_batch.get(fn),
            "serial": filename_to_serial.get(fn),
            "review": dec.model_dump(),
        }
        if fn in ocr_by_file:
            entry["ocr"] = ocr_by_file[fn].model_dump()
        if fn in extractions:
            entry["extraction"] = extractions[fn].model_dump()
        write_sidecar(dst, entry)

    name_cache = load_name_cache(output_path)
    for fn in file_destinations:
        ext = extractions.get(fn)
        dec = decisions[fn]
        if isinstance(ext, ReceiptResult):
            extracted = ext.name
        elif isinstance(ext, OtherResult):
            extracted = ext.title
        else:
            extracted = ""
        name_cache[fn] = {"extracted": extracted, "confirmed": dec.name}
    save_name_cache(output_path, name_cache)

    all_now_organized = scan_organized_filenames(output_path)
    remaining = {fn for fn in all_decisions if fn not in all_now_organized}
    if not remaining:
        cleaned = []
        for artifact in CLEANUP_ARTIFACTS:
            p = output_path / artifact
            if p.exists():
                p.unlink()
                cleaned.append(artifact)
        cleanup_msg = f" Cleaned up {', '.join(cleaned)}." if cleaned else ""
        st.success(f"Archived {len(file_destinations)} files. All files organized!{cleanup_msg}")
    else:
        st.success(f"Archived {len(file_destinations)} files. {len(remaining)} file(s) still pending.")
