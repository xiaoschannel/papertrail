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
from models import (
    OcrResult,
    OtherResult,
    ReceiptResult,
    batch_serial_key,
    iter_indexed_files,
    load_scan_index,
    parse_batch_serial_key,
)
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
index_file = output_path / "batches.json"
if not index_file.exists():
    st.info("Run File Index first to create batches.json.")
    st.stop()

scan_index = load_scan_index(output_path)
key_to_filename = {batch_serial_key(bid, ser): fn for bid, ser, fn in iter_indexed_files(scan_index, include_archived=False)}
loaded = load_ocr_results(output_path)
ocr_by_key: dict[str, OcrResult] = {k: r for k, r in loaded.items() if r.succeeded}
extractions = load_extractions(output_path)
all_decisions = load_decisions(output_path)
organized = scan_organized_filenames(output_path)

decisions = {k: dec for k, dec in all_decisions.items() if k in key_to_filename and key_to_filename[k] not in organized}

complete_batches = []
for batch in scan_index.batches:
    if batch.archived:
        continue
    batch_keys = {batch_serial_key(batch.batch_id, serial) for serial in batch.files}
    if batch_keys.issubset(decisions.keys()):
        complete_batches.append(batch)

decisions_to_archive = {k: dec for k, dec in decisions.items() if any(k in {batch_serial_key(b.batch_id, s) for s in b.files} for b in complete_batches)}

st.metric("Complete batches to archive", len(complete_batches))
st.metric("Files to archive", len(decisions_to_archive))

if not complete_batches:
    incomplete = [b for b in scan_index.batches if not b.archived]
    if incomplete:
        st.info("No complete batches. Review all files in a batch before archiving.")
    else:
        st.info("No new files to organize.")
    st.stop()

existing_names_by_folder = scan_existing_names(output_path)

accepted_decisions = {k: dec for k, dec in decisions_to_archive.items() if dec.verdict == "accepted"}
marked = [k for k, dec in decisions_to_archive.items() if dec.verdict == "marked"]
tossed = [k for k, dec in decisions_to_archive.items() if dec.verdict not in ("accepted", "marked")]

file_destinations = plan_accepted_destinations(
    accepted_decisions,
    existing_names_by_folder,
    key_to_filename=key_to_filename,
    key_to_sort={k: (parse_batch_serial_key(k) or (0, 0)) for k in accepted_decisions},
)
for k in marked:
    file_destinations[k] = f"marked/{key_to_filename[k]}"
for k in tossed:
    file_destinations[k] = f"tossed/{key_to_filename[k]}"

n_accepted = len(accepted_decisions)
n_marked = len(marked)
n_tossed = len(tossed)

col0, col1, col2 = st.columns(3)
col0.metric("Accepted", n_accepted)
col1.metric("Marked", n_marked)
col2.metric("Tossed", n_tossed)

st.subheader("Preview")
preview_data = [
    {"Original": key_to_filename.get(k, k), "Destination": dest}
    for k, dest in sorted(file_destinations.items(), key=lambda x: (parse_batch_serial_key(x[0]) or (0, 0), x[0]))
]
st.dataframe(preview_data, hide_index=True, width="stretch")

CLEANUP_ARTIFACTS = ["ocr.json", "extractions.json", "decisions.json"]

if st.button("Archive", width="stretch", type="primary"):
    for key, dest in file_destinations.items():
        fn = key_to_filename[key]
        src = input_path / fn
        dst = output_path / dest
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(src), str(dst))

        dec = decisions_to_archive[key]
        parsed = parse_batch_serial_key(key)
        entry: dict = {
            "original_filename": fn,
            "batch_id": parsed[0] if parsed else None,
            "serial": parsed[1] if parsed else None,
            "review": dec.model_dump(),
        }
        if key in ocr_by_key:
            entry["ocr"] = ocr_by_key[key].model_dump()
        if key in extractions:
            entry["extraction"] = extractions[key].model_dump()
        write_sidecar(dst, entry)

    name_cache = load_name_cache(output_path)
    for key in file_destinations:
        ext = extractions.get(key)
        dec = decisions_to_archive[key]
        if isinstance(ext, ReceiptResult):
            extracted = ext.name
        elif isinstance(ext, OtherResult):
            extracted = ext.title
        else:
            extracted = ""
        name_cache[key] = {"extracted": extracted, "confirmed": dec.name}
    save_name_cache(output_path, name_cache)

    for batch in complete_batches:
        batch.archived = True
    (output_path / "batches.json").write_text(scan_index.model_dump_json(indent=2), encoding="utf-8")

    if all(b.archived for b in scan_index.batches):
        cleaned = []
        for artifact in CLEANUP_ARTIFACTS:
            p = output_path / artifact
            if p.exists():
                p.unlink()
                cleaned.append(artifact)
        cleanup_msg = f" Cleaned up {', '.join(cleaned)}." if cleaned else ""
        st.success(f"Archived {len(file_destinations)} files. All batches archived!{cleanup_msg}")
    else:
        st.success(f"Archived {len(file_destinations)} files from {len(complete_batches)} batch(es).")
