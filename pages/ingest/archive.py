import shutil
from pathlib import Path

import streamlit as st

from data import (
    build_document_index,
    load_decisions,
    load_extractions,
    load_ocr_results,
    load_smart_match_cache,
    save_smart_match_cache,
    scan_organized_filenames,
    write_sidecar,
)
from models import (
    DocumentKey,
    OcrResult,
    OtherResult,
    ReceiptResult,
    Sidecar,
    batch_serial_key,
    iter_indexed_files,
    load_scan_index,
    parse_batch_serial_key,
)
from organize_utils import plan_accepted_destinations, scan_existing_names
from settings import get_config

st.title("Archive")

cfg = get_config()
batch_dir = cfg.batch_output_path
image_dir = cfg.input_image_path

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
indexed_keys = {batch_serial_key(batch_id, serial) for batch_id, serial, _ in iter_indexed_files(scan_index, include_archived=False)}
key_to_filename = {batch_serial_key(batch_id, serial): fn for batch_id, serial, fn in iter_indexed_files(scan_index, include_archived=False)}
loaded = load_ocr_results(output_path)
ocr_by_key: dict[str, OcrResult] = {k: r for k, r in loaded.items() if r.succeeded}
extractions = load_extractions(output_path)
all_decisions = load_decisions(output_path)
organized = scan_organized_filenames(output_path)

index = build_document_index(output_path, indexed_keys)

complete_batches = []
for batch in scan_index.batches:
    if batch.archived:
        continue
    batch_keys = {batch_serial_key(batch.batch_id, serial) for serial in batch.files}
    if all(str(index.key_to_doc_key(k)) in all_decisions for k in batch_keys):
        complete_batches.append(batch)

doc_keys_to_archive = set()
for b in complete_batches:
    for serial in b.files:
        k = batch_serial_key(b.batch_id, serial)
        doc_keys_to_archive.add(str(index.key_to_doc_key(k)))

decisions_to_archive = {doc_key: all_decisions[doc_key] for doc_key in doc_keys_to_archive if doc_key in all_decisions}

decisions_by_doc = {DocumentKey.parse(k): v for k, v in decisions_to_archive.items() if DocumentKey.parse(k)}
records_expanded = {
    k: v for k, v in index.expand_decisions(decisions_by_doc).items()
    if k in key_to_filename and key_to_filename[k] not in organized
}

non_archived = [b for b in scan_index.batches if not b.archived]
all_complete = len(complete_batches) == len(non_archived) and len(non_archived) > 0

n_documents = len(doc_keys_to_archive)
n_multipage = sum(1 for doc_key in doc_keys_to_archive if (p := DocumentKey.parse(doc_key)) and len(index.keys_for_doc(p)) > 1)

accepted_doc_keys = [doc_key for doc_key, decision in decisions_to_archive.items() if decision.verdict == "accepted"]
marked_doc_keys = [doc_key for doc_key, decision in decisions_to_archive.items() if decision.verdict == "marked"]
tossed_doc_keys = [doc_key for doc_key, decision in decisions_to_archive.items() if decision.verdict not in ("accepted", "marked")]

accepted_decisions = {DocumentKey.parse(doc_key): decisions_to_archive[doc_key] for doc_key in accepted_doc_keys if DocumentKey.parse(doc_key)}
accepted_records = {
    k: v for k, v in index.expand_decisions(accepted_decisions).items()
    if k in key_to_filename and key_to_filename[k] not in organized
}

n_accepted = len(accepted_records)
n_marked = sum(len(index.keys_for_doc(DocumentKey.parse(doc_key) or DocumentKey.from_group([doc_key]))) for doc_key in marked_doc_keys)
n_tossed = sum(len(index.keys_for_doc(DocumentKey.parse(doc_key) or DocumentKey.from_group([doc_key]))) for doc_key in tossed_doc_keys)

c0, c1, c2, _ = st.columns(4)
c0.metric("Batches complete", f"{len(complete_batches)} / {len(non_archived)}")
c1.metric("Documents", n_documents)
c2.metric("Multipage", n_multipage)
c3, c4, c5, c6 = st.columns(4)
c3.metric("Files", len(records_expanded))
c4.metric("Accepted", n_accepted)
c5.metric("Marked", n_marked)
c6.metric("Tossed", n_tossed)

if not all_complete:
    if non_archived:
        st.info("Review all files before archiving.")
    else:
        st.info("No new files to organize.")
    st.stop()

existing_names_by_folder = scan_existing_names(output_path)

file_destinations = plan_accepted_destinations(
    accepted_records,
    existing_names_by_folder,
    key_to_filename=key_to_filename,
    key_to_sort={k: (parse_batch_serial_key(k) or (0, 0)) for k in accepted_records},
)
for doc_key in marked_doc_keys:
    doc_key_parsed = DocumentKey.parse(doc_key) or DocumentKey.from_group([doc_key])
    for key in index.keys_for_doc(doc_key_parsed):
        if key in key_to_filename and key_to_filename[key] not in organized:
            file_destinations[key] = f"marked/{key_to_filename[key]}"
for doc_key in tossed_doc_keys:
    doc_key_parsed = DocumentKey.parse(doc_key) or DocumentKey.from_group([doc_key])
    for key in index.keys_for_doc(doc_key_parsed):
        if key in key_to_filename and key_to_filename[key] not in organized:
            file_destinations[key] = f"tossed/{key_to_filename[key]}"

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

        doc_key = str(index.key_to_doc_key(key))
        decision = decisions_to_archive[doc_key]
        parsed = parse_batch_serial_key(key)
        doc_key_obj = index.key_to_doc_key(key)
        sidecar = Sidecar(
            original_filename=fn,
            batch_id=parsed[0] if parsed else None,
            serial=parsed[1] if parsed else None,
            review=decision,
            document_key=doc_key if doc_key_obj.is_multi_page else None,
            ocr=ocr_by_key.get(key),
            extraction=extractions.get(doc_key),
        )
        write_sidecar(dst, sidecar)

    smart_match_cache = load_smart_match_cache(output_path)
    for doc_key in doc_keys_to_archive:
        extraction = extractions.get(doc_key)
        decision = decisions_to_archive[doc_key]
        extracted_phone = ""
        if isinstance(extraction, ReceiptResult):
            extracted = extraction.name
            extracted_phone = extraction.phone
        elif isinstance(extraction, OtherResult):
            extracted = extraction.title
        else:
            extracted = ""
        smart_match_cache[doc_key] = {
            "extracted": extracted,
            "confirmed": decision.name,
            "extracted_phone": extracted_phone,
        }
    save_smart_match_cache(output_path, smart_match_cache)

    for batch in complete_batches:
        batch.archived = True
    (output_path / "batches.json").write_text(scan_index.model_dump_json(indent=2), encoding="utf-8")

    cleaned = []
    for artifact in CLEANUP_ARTIFACTS:
        p = output_path / artifact
        if p.exists():
            p.unlink()
            cleaned.append(artifact)
    cleanup_msg = f" Cleaned up {', '.join(cleaned)}." if cleaned else ""
    st.success(f"Archived {len(file_destinations)} files.{cleanup_msg}")
