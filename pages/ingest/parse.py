import random
import time
from pathlib import Path

import streamlit as st

from data import (
    build_document_index,
    load_decisions,
    load_extractions,
    load_ocr_results,
    save_extractions,
)
from extraction import EXTRACTORS
from models import OcrResult, batch_serial_key, iter_indexed_files, load_scan_index
from settings import get_config, update_config
from streamlit_progress import ProgressBar

st.title("Parse")

cfg = get_config()
batch_dir = cfg.batch_output_path

if not batch_dir:
    st.stop()

output_path = Path(batch_dir)
index_file = output_path / "batches.json"
if not index_file.exists():
    st.info("Run File Index first to create batches.json.")
    st.stop()

scan_index = load_scan_index(output_path)
indexed_keys = {batch_serial_key(batch_id, serial) for batch_id, serial, _ in iter_indexed_files(scan_index, include_archived=False)}
loaded = load_ocr_results(output_path)
ocr_by_key = {k: r.markdown for k, r in loaded.items() if r.succeeded and k in indexed_keys}
ocr_results_by_key: dict[str, OcrResult] = {k: r for k, r in loaded.items() if r.succeeded and k in indexed_keys}

index = build_document_index(output_path, indexed_keys, ocr_keys=set(ocr_by_key))
raw_doc_keys_with_ocr = index.doc_keys_with_ocr(ocr_by_key)
decisions = load_decisions(output_path)
doc_keys_with_ocr = [
    dk
    for dk in raw_doc_keys_with_ocr
    if not (decisions.get(str(dk)) and decisions[str(dk)].verdict == "tossed")
]
extractions = {k: v for k, v in load_extractions(output_path).items() if k in {str(doc_key) for doc_key in doc_keys_with_ocr}}

extractors = list(EXTRACTORS.keys())
default_extractor_idx = extractors.index(cfg.extractor_model) if cfg.extractor_model in extractors else 0

def _save_extractor():
    update_config(extractor_model=st.session_state["parse_extractor"])


def _save_parse_custom_instruction():
    update_config(parse_custom_instruction=st.session_state["parse_custom_instruction"])


extractor_name = st.selectbox("Model", extractors, index=default_extractor_idx, key="parse_extractor", on_change=_save_extractor)

st.text_area(
    "Custom instructions (optional)",
    value=cfg.parse_custom_instruction,
    height=160,
    key="parse_custom_instruction",
    on_change=_save_parse_custom_instruction,
)

mode = st.radio("Mode", ["Process new only", "Reprocess all"], horizontal=True)
if mode == "Reprocess all":
    to_process = list(doc_keys_with_ocr)
    existing_extractions = {}
else:
    existing_extractions = dict(extractions)
    to_process = [doc_key for doc_key in doc_keys_with_ocr if str(doc_key) not in existing_extractions]

n_total = len(raw_doc_keys_with_ocr)
n_tossed = n_total - len(doc_keys_with_ocr)
n_processed = len(extractions)
n_to_process = len(doc_keys_with_ocr) - n_processed

batch_limit = st.number_input("Batch size (0 = all)", min_value=0, value=0, step=1)
if batch_limit > 0:
    to_process = to_process[:batch_limit]

col0, col1, col2, col3 = st.columns(4)
col0.metric("Total Documents", n_total)
col1.metric("Processed", n_processed)
col2.metric("Tossed", n_tossed)
col3.metric("To process", n_to_process)

if mode == "Reprocess all":
    st.warning("This will replace all existing extractions. This cannot be undone.")
    reprocess_confirmed = st.checkbox("I understand, proceed with reprocess")
    run_clicked = st.button("Run Extraction", width="stretch", type="primary", disabled=not reprocess_confirmed)
else:
    run_clicked = st.button("Run Extraction", width="stretch", type="primary")

if run_clicked and to_process:
    if mode == "Reprocess all":
        extractions.clear()

    parse_custom_instruction = st.session_state.get(
        "parse_custom_instruction", cfg.parse_custom_instruction
    )
    random.shuffle(to_process)
    bar = ProgressBar(len(to_process))
    failed: list[str] = []
    last_save = time.time()

    for doc_key in to_process:
        ocr_text, has_boxes = index.concat_ocr_with_boxes(doc_key, ocr_results_by_key)
        try:
            extractions[str(doc_key)] = EXTRACTORS[extractor_name](
                ocr_text,
                has_boxes=has_boxes,
                custom_instruction=parse_custom_instruction,
            )
            bar.tick(True)
        except Exception:
            failed.append(str(doc_key))
            bar.tick(False)
        if time.time() - last_save > 15:
            save_extractions(output_path, extractions)
            last_save = time.time()
    save_extractions(output_path, extractions)

    if failed:
        st.warning(f"{len(failed)} / {len(to_process)} extraction(s) failed: {', '.join(failed)}")
    st.rerun()

if run_clicked and not to_process:
    st.info("No items to process.")

if not raw_doc_keys_with_ocr:
    st.info("No OCR results. Run OCR first.")
